/**
 * EntityTable — virtualised, sortable, multi-select table over AI Extraction
 * NER entities. Renders the same 10-column layout as the PyQt6
 * ExtractionEditor (Rule 41 schema + Rule 49 §E confidence tooltips +
 * Rule 52 AI verdict column).
 *
 * - Header left-click sorts; header right-click opens ColumnFilterPopup
 * - Inline <select> editors for Type and Role
 * - Approved checkbox calls ExtractionApprovals.patch immediately
 * - Rows past 200 are virtualised via @tanstack/react-virtual
 */

import {
  ChangeEvent,
  CSSProperties,
  MouseEvent as ReactMouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { AiVerdictPill } from "@/components/extraction/AiVerdictPill";
import { ColumnFilterPopup } from "@/components/extraction/ColumnFilterPopup";
import {
  ENTITY_ROLES,
  ENTITY_TYPES,
  Entity,
  EntityRole,
  EntityType,
  ExtractionApprovals,
} from "@/api/extractionApprovals";
import { recheckEntity } from "@/api/nerVerify";
import { HistoryTimeline } from "@/components/history/HistoryTimeline";
import { langOf } from "@/utils/hebrew";
import {Glass} from "@/components/glass";

export type SortDirection = "asc" | "desc";

export interface SortState {
  column: ColumnKey;
  direction: SortDirection;
}

export type ColumnKey =
  | "control_number"
  | "text"
  | "type"
  | "role"
  | "source"
  | "confidence"
  | "model_confidence"
  | "exists_in"
  | "ai_verdict"
  | "fix"
  | "approved"
  | "edit";

interface ColumnDef {
  key: ColumnKey;
  label: string;
  width: string;
  sortable: boolean;
  filterable: boolean;
}

const COLUMNS: ColumnDef[] = [
  // Widths are tuned to fit a ~1280px viewport without horizontal
  // scroll. Type/Role are deliberately wide enough for the longest
  // enum value (WORK_AUTHOR / TRANSCRIBER / COMMENTATOR).
  { key: "control_number", label: "MS", width: "minmax(110px, 130px)", sortable: true, filterable: true },
  { key: "text", label: "Text", width: "minmax(180px, 1.4fr)", sortable: true, filterable: false },
  { key: "type", label: "Type", width: "140px", sortable: true, filterable: true },
  { key: "role", label: "Role", width: "140px", sortable: true, filterable: true },
  { key: "source", label: "Source", width: "130px", sortable: true, filterable: true },
  { key: "confidence", label: "Conf.", width: "70px", sortable: true, filterable: true },
  { key: "model_confidence", label: "Model", width: "70px", sortable: true, filterable: false },
  { key: "exists_in", label: "MARC", width: "100px", sortable: true, filterable: true },
  { key: "ai_verdict", label: "AI Check", width: "110px", sortable: true, filterable: true },
  { key: "fix", label: "Fix", width: "80px", sortable: false, filterable: false },
  { key: "approved", label: "✓", width: "44px", sortable: true, filterable: true },
  { key: "edit", label: "", width: "96px", sortable: false, filterable: false },
];

export interface EntityTableProps {
  runId: string;
  projectId: string;
  entities: Entity[];
  selectedIds: Set<string>;
  onSelectionChange: (ids: Set<string>) => void;
  onEntityUpdated: (entity: Entity) => void;
  onOpenEdit: (entity: Entity) => void;
  onViewSource: (entity: Entity) => void;
  onOpenMarc?: (controlNumber: string) => void;
  onOpenVerdict?: (entity: Entity) => void;
  columnFilters: Record<string, Set<string>>;
  onColumnFiltersChange: (filters: Record<string, Set<string>>) => void;
  onVisibleCountChange?: (count: number, entities: Entity[]) => void;
  globalSearch?: string;
}

function confidenceBand(value: number | null): string {
  if (value === null) return "—";
  if (value >= 0.85) return "high";
  if (value >= 0.6) return "medium";
  return "low";
}

const _EXISTS_BADGE: Record<string, { label: string; bg: string; fg: string; glyph: string }> = {
  grounded:    { label: "grounded", bg: "rgba(120,200,140,0.15)", fg: "#7adf95", glyph: "✓" },
  wrong_field: { label: "wrong field", bg: "rgba(253,224,71,0.18)", fg: "#fde047", glyph: "⚠" },
  novel:       { label: "novel",    bg: "rgba(127,196,255,0.15)", fg: "#77cce5", glyph: "✨" },
  unknown:     { label: "—",        bg: "transparent",            fg: "var(--muted)", glyph: "—" },
};

function cellValueFor(entity: Entity, column: ColumnKey): string {
  switch (column) {
    case "control_number": return entity.control_number;
    case "text":           return entity.text;
    case "type":           return entity.type;
    case "role":           return entity.role || "—";
    case "source":         return entity.source;
    case "confidence":     return confidenceBand(entity.confidence);
    case "model_confidence":
      return entity.model_confidence !== null ? entity.model_confidence.toFixed(2) : "—";
    case "exists_in":      return entity.exists_in?.status ?? "unknown";
    case "ai_verdict":     return String(entity.ai_verdict?.overall ?? "unknown");
    case "fix":            return "";
    case "approved":       return entity.approved ? "yes" : "no";
    default:               return "";
  }
}

function applyColumnFilters(
  entities: Entity[],
  filters: Record<string, Set<string>>,
): Entity[] {
  const activeKeys = Object.keys(filters).filter(
    (k) => filters[k] && filters[k].size > 0,
  );
  if (activeKeys.length === 0) return entities;
  return entities.filter((entity) =>
    activeKeys.every((k) => filters[k].has(cellValueFor(entity, k as ColumnKey))),
  );
}

function applySort(entities: Entity[], sort: SortState | null): Entity[] {
  if (!sort) return entities;
  const sorted = [...entities];
  const dir = sort.direction === "asc" ? 1 : -1;
  sorted.sort((a, b) => {
    const av = cellValueFor(a, sort.column);
    const bv = cellValueFor(b, sort.column);
    if (sort.column === "confidence" || sort.column === "model_confidence") {
      const an = parseFloat(av);
      const bn = parseFloat(bv);
      if (Number.isNaN(an) && Number.isNaN(bn)) return 0;
      if (Number.isNaN(an)) return 1;
      if (Number.isNaN(bn)) return -1;
      return (an - bn) * dir;
    }
    return av.localeCompare(bv) * dir;
  });
  return sorted;
}

export function EntityTable(props: EntityTableProps) {
  const {
    runId, projectId, entities, selectedIds, onSelectionChange,
    onEntityUpdated, onOpenEdit, onViewSource, onOpenMarc, onOpenVerdict,
    columnFilters, onColumnFiltersChange, onVisibleCountChange, globalSearch,
  } = props;

  const [sort, setSort] = useState<SortState | null>(null);
  const [popup, setPopup] = useState<{ column: ColumnKey; x: number; y: number } | null>(null);
  const [historyFor, setHistoryFor] = useState<{ id: string } | null>(null);

  // Per-column free-text filter — the user wants header-row search
  // boxes on MS + Text alongside the right-click popup for enum
  // columns. Keys are ColumnKey strings; value is the substring
  // to match. AND-combines with the popup-based columnFilters.
  const [textFilters, setTextFilters] = useState<Partial<Record<ColumnKey, string>>>({});

  // Entities currently being re-verified after an Auto-fix (Set of ids).
  const [recheckingIds, setRecheckingIds] = useState<ReadonlySet<string>>(new Set());

  const parentRef = useRef<HTMLDivElement | null>(null);

  const filtered = useMemo(() => {
    let out = applyColumnFilters(entities, columnFilters);
    const needle = (globalSearch ?? "").trim().toLocaleLowerCase();
    if (needle) {
      out = out.filter((e) =>
        e.text.toLocaleLowerCase().includes(needle) ||
        e.control_number.toLocaleLowerCase().includes(needle),
      );
    }
    for (const [col, n] of Object.entries(textFilters)) {
      const needle2 = (n ?? "").trim().toLocaleLowerCase();
      if (!needle2) continue;
      out = out.filter((e) =>
        cellValueFor(e, col as ColumnKey).toLocaleLowerCase().includes(needle2),
      );
    }
    return out;
  }, [entities, columnFilters, globalSearch, textFilters]);
  const display = useMemo(() => applySort(filtered, sort), [filtered, sort]);

  useEffect(() => {
    onVisibleCountChange?.(display.length, display);
  }, [display, onVisibleCountChange]);

  const handleHeaderClick = useCallback((key: ColumnKey, sortable: boolean) => {
    if (!sortable) return;
    setSort((prev) => {
      if (!prev || prev.column !== key) return { column: key, direction: "asc" };
      if (prev.direction === "asc") return { column: key, direction: "desc" };
      return null;
    });
  }, []);

  const handleHeaderContext = useCallback(
    (event: ReactMouseEvent<HTMLDivElement>, key: ColumnKey, filterable: boolean) => {
      event.preventDefault();
      if (!filterable) return;
      setPopup({ column: key, x: event.clientX, y: event.clientY });
    }, [],
  );

  const distinctValues = useMemo<string[]>(() => {
    if (!popup) return [];
    const values = new Set<string>();
    for (const e of entities) values.add(cellValueFor(e, popup.column));
    return Array.from(values).sort();
  }, [popup, entities]);

  const handlePopupApply = useCallback((selected: Set<string>) => {
    if (!popup) return;
    const next = { ...columnFilters };
    if (selected.size === 0) delete next[popup.column];
    else next[popup.column] = selected;
    onColumnFiltersChange(next);
    setPopup(null);
  }, [popup, columnFilters, onColumnFiltersChange]);

  const toggleSelect = useCallback((id: string) => {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    onSelectionChange(next);
  }, [selectedIds, onSelectionChange]);

  const toggleSelectAllVisible = useCallback(() => {
    const visibleIds = display.map((e) => e.id);
    const allSelected = visibleIds.every((id) => selectedIds.has(id));
    const next = new Set(selectedIds);
    if (allSelected) { for (const id of visibleIds) next.delete(id); }
    else             { for (const id of visibleIds) next.add(id); }
    onSelectionChange(next);
  }, [display, selectedIds, onSelectionChange]);

  const updateEntity = useCallback(
    async (entity: Entity, patch: { type?: EntityType; role?: EntityRole; approved?: boolean }) => {
      try {
        const updated = await ExtractionApprovals.patch(runId, entity.id, patch);
        onEntityUpdated(updated);
      } catch (err) {
        console.error("Failed to patch entity", err);
      }
    }, [runId, onEntityUpdated],
  );

  const applyAutoFix = useCallback(
    async (entity: Entity, fixText: string) => {
      // Mark re-checking up front so the Fix button gives way to the
      // "re-checking…" indicator while we patch + re-judge.
      setRecheckingIds((prev) => new Set(prev).add(entity.id));
      let updated: Entity = entity;
      try {
        // 1. Patch only the text override — deliberately does NOT touch approved.
        updated = await ExtractionApprovals.patch(runId, entity.id, { text: fixText });
        // 2. Re-run the AI check for this one entity against the corrected
        //    text. The fix already succeeded; a failed re-check is non-fatal.
        await recheckEntity(runId, entity.id);
      } catch (err) {
        console.error("Failed to apply auto-fix", err);
      } finally {
        // 3. A SINGLE refetch after both steps — the store coalesces
        //    overlapping fetches, so one refresh here reliably picks up the
        //    new text AND the updated verdict (two would race + drop one).
        onEntityUpdated(updated);
        setRecheckingIds((prev) => {
          const next = new Set(prev);
          next.delete(entity.id);
          return next;
        });
      }
    }, [runId, onEntityUpdated],
  );

  const gridTemplate = useMemo(
    () => ["40px", ...COLUMNS.map((c) => c.width)].join(" "),
    [],
  );

  const renderRow = (entity: Entity, style?: CSSProperties) => {
    const selected = selectedIds.has(entity.id);
    const exists = _EXISTS_BADGE[entity.exists_in?.status ?? "unknown"] ?? _EXISTS_BADGE.unknown;

    // Auto-fix: show button only when:
    // 1. Entity has a suggested_fix with confidence="high"
    // 2. Entity is a NER entity (not genre_ml)
    // 3. The suggested text differs from the current effective text
    const fix = entity.ai_verdict?.suggested_fix;
    const effectiveText = (entity.effective_text ?? entity.text ?? "").trim();
    const isRechecking = recheckingIds.has(entity.id);
    const showAutoFix =
      !isRechecking &&
      fix != null &&
      fix.confidence === "high" &&
      entity.source !== "genre_ml" &&
      fix.text.trim() !== effectiveText;
    return (
      <div
        key={entity.id}
        data-testid="entity-row"
        data-entity-id={entity.id}
        data-control-number={entity.control_number}
        style={{ ...style, gridTemplateColumns: gridTemplate }}
        className={`grid items-center gap-2 border-b border-white/5 px-2 py-1 text-sm text-ink ${selected ? "bg-biu-sky/10" : "hover:bg-white/5"}`}
      >
        <input
          type="checkbox"
          aria-label={`Select ${entity.text}`}
          data-testid="entity-select"
          checked={selected}
          onChange={() => toggleSelect(entity.id)}
          className="h-4 w-4 accent-biu-sky"
        />
        <button type="button"
                onClick={() => onOpenMarc?.(entity.control_number)}
                className="truncate text-left font-mono text-xs muted hover:text-ink"
                title={entity.control_number}>
          {entity.control_number}
        </button>
        <button type="button" onClick={() => onViewSource(entity)}
                className="truncate text-left hover:underline" dir="auto"
                lang={langOf(entity.text)}
                title={entity.text}
                data-testid="entity-text-link">
          {entity.text}
        </button>
        <select
          value={entity.type}
          onChange={(e: ChangeEvent<HTMLSelectElement>) => updateEntity(entity, { type: e.target.value as EntityType })}
          aria-label="Entity type"
          data-testid="entity-type"
          className="h-7 w-full rounded-md border border-white/10 bg-black/30 px-2 text-xs text-ink outline-none focus:border-biu-sky"
        >
          {ENTITY_TYPES.map((t) => (<option key={t} value={t}>{t}</option>))}
        </select>
        <select
          value={entity.role}
          onChange={(e: ChangeEvent<HTMLSelectElement>) => updateEntity(entity, { role: e.target.value as EntityRole })}
          aria-label="Entity role"
          data-testid="entity-role"
          className="h-7 w-full rounded-md border border-white/10 bg-black/30 px-2 text-xs text-ink outline-none focus:border-biu-sky"
        >
          {ENTITY_ROLES.map((r) => (<option key={r || "_blank"} value={r}>{r || "—"}</option>))}
        </select>
        <span className="truncate text-xs muted">{entity.source}</span>
        <span className="text-xs"
              title={`Keyword classifier: ${entity.confidence !== null ? entity.confidence.toFixed(2) : "—"}`}>
          {confidenceBand(entity.confidence)}
        </span>
        <span className="text-xs muted">
          {entity.model_confidence !== null ? entity.model_confidence.toFixed(2) : "—"}
        </span>
        <span title={entity.exists_in?.note ?? ""}
              data-testid="exists-in"
              data-status={entity.exists_in?.status ?? "unknown"}
              className="inline-flex items-center rounded-full text-[10px] py-0.5 px-2"
              style={{ background: exists.bg, color: exists.fg, border: `1px solid ${exists.fg}33` }}>
          <span aria-hidden style={{ marginRight: 3 }}>{exists.glyph}</span>
          {exists.label}
        </span>
        <div data-testid="ai-verdict-cell">
          <AiVerdictPill
            verdict={entity.ai_verdict}
            onClick={onOpenVerdict ? () => onOpenVerdict(entity) : undefined}
            size="sm"
          />
        </div>
        <div data-testid="entity-fix-cell">
          {showAutoFix ? (
            <button
              type="button"
              data-testid="entity-autofix-btn"
              title={fix!.reasoning ?? `Auto-fix: apply AI-suggested correction → ${fix!.text}`}
              onClick={() => applyAutoFix(entity, fix!.text)}
              className="button-ghost h-7 px-2 text-xs text-warn hover:opacity-90 whitespace-nowrap"
            >
              ✨ Fix
            </button>
          ) : isRechecking ? (
            <span
              data-testid="entity-rechecking"
              title="Applied fix — re-running AI verification…"
              className="inline-flex items-center h-7 px-1 text-xs text-warn opacity-80 animate-pulse whitespace-nowrap"
            >
              ⏳…
            </span>
          ) : null}
        </div>
        <input
          type="checkbox"
          aria-label={`Approve ${entity.text}`}
          data-testid="entity-approved"
          checked={entity.approved}
          onChange={(e) => updateEntity(entity, { approved: e.target.checked })}
          className="h-4 w-4 accent-biu-sky"
        />
        <div className="flex gap-1">
          <button type="button" className="button-ghost h-7 px-2 text-xs"
                  data-testid="entity-edit-btn"
                  title="Edit entity"
                  onClick={() => onOpenEdit(entity)}>✎</button>
          <button type="button" className="button-ghost h-7 px-2 text-xs"
                  data-testid="entity-view-source"
                  title="View MARC source"
                  onClick={() => onViewSource(entity)}>👁</button>
          {projectId ? (
            <button
              type="button"
              data-testid={`history-button-${entity.id}`}
              onClick={() => setHistoryFor({ id: String(entity.id) })}
              aria-label="View edit history"
              title="View edit history"
              className="button-ghost h-7 px-2 text-xs"
            >📜</button>
          ) : null}
        </div>
      </div>
    );
  };

  const allVisibleSelected = display.length > 0 && display.every((e) => selectedIds.has(e.id));

  // Single horizontally-scrollable container holds header + filter
  // row + body so they all scroll together. Header + filter row are
  // pinned vertically with position:sticky so they stay visible while
  // the curator scrolls through rows; horizontally they share the
  // same scroll axis as the body so columns line up at every offset.
  // ``min-w-max`` on the inner block forces the grid to its full
  // intrinsic width, triggering horizontal scroll when the viewport
  // is narrower than the column total.
  return (
    <Glass className="overflow-hidden" data-testid="entity-table">
      <div className="max-h-[640px] overflow-auto" ref={parentRef}>
       <div className="min-w-max">
      <div
        className="sticky top-0 z-20 grid items-center gap-2 border-b border-white/10 bg-[rgba(16,24,36,0.92)] backdrop-blur px-2 py-2 text-xs uppercase tracking-wide kicker"
        style={{ gridTemplateColumns: gridTemplate }}
      >
        <input
          type="checkbox"
          aria-label="Select all visible"
          data-testid="select-all-visible"
          checked={allVisibleSelected}
          onChange={toggleSelectAllVisible}
          className="h-4 w-4 accent-biu-sky"
        />
        {COLUMNS.map((col) => {
          const isSorted = sort?.column === col.key;
          const arrow = isSorted ? (sort?.direction === "asc" ? " ▲" : " ▼") : "";
          const filterActive = (columnFilters[col.key]?.size ?? 0) > 0;
          return (
            <div
              key={col.key}
              data-testid={`col-${col.key}`}
              data-sort-direction={isSorted ? sort.direction : "none"}
              className={`flex items-center justify-between gap-1 overflow-hidden ${filterActive ? "text-biu-sky" : ""}`}
              onContextMenu={(e) => handleHeaderContext(e, col.key, col.filterable)}
              title={col.filterable ? "Right-click to filter" : col.label}
            >
              <span
                onClick={() => handleHeaderClick(col.key, col.sortable)}
                className={`flex-1 ${col.sortable ? "cursor-pointer" : "cursor-default"} select-none truncate`}
              >
                {col.label}{arrow}
              </span>
              {col.filterable && (
                <button
                  type="button"
                  data-testid={`filter-btn-${col.key}`}
                  aria-label={`Filter ${col.label}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    const rect = e.currentTarget.getBoundingClientRect();
                    setPopup({column: col.key, x: rect.left, y: rect.bottom + 4});
                  }}
                  className={`shrink-0 h-5 w-5 rounded text-[11px] hover:bg-white/10 ${filterActive ? "text-biu-sky" : "muted"}`}
                >▾</button>
              )}
            </div>
          );
        })}
      </div>
      <div
        className="sticky top-[42px] z-10 grid items-center gap-1 border-b border-white/10 bg-[rgba(16,24,36,0.85)] backdrop-blur px-2 py-1 text-xs"
        style={{ gridTemplateColumns: gridTemplate }}
      >
        <span />
        {COLUMNS.map((col) => {
          if (col.key === "control_number" || col.key === "text") {
            return (
              <input
                key={col.key}
                type="text"
                placeholder={`filter ${col.label}…`}
                value={textFilters[col.key] ?? ""}
                onChange={(e) =>
                  setTextFilters((prev) => ({...prev, [col.key]: e.target.value}))
                }
                aria-label={`filter ${col.label}`}
                data-testid={`text-filter-${col.key}`}
                className="h-6 w-full rounded-md border border-white/10 bg-black/30 px-2 text-[11px] text-ink outline-none focus:border-biu-sky"
              />
            );
          }
          return <span key={col.key} />;
        })}
      </div>
      {display.map((entity) => renderRow(entity))}
      {display.length === 0 ? (
        <div className="px-4 py-8 text-center text-sm muted" data-testid="entity-table-empty">
          No entities match the current filters.
        </div>
      ) : null}
       </div>{/* /min-w-max */}
      </div>{/* /scroll container */}
      {popup ? (
        <ColumnFilterPopup
          columnLabel={COLUMNS.find((c) => c.key === popup.column)?.label ?? ""}
          values={distinctValues}
          selected={columnFilters[popup.column] ?? new Set<string>()}
          x={popup.x}
          y={popup.y}
          onApply={handlePopupApply}
          onCancel={() => setPopup(null)}
        />
      ) : null}
      {historyFor ? (
        <Glass as="aside" variant="drawer" className="fixed right-0 top-0 h-full w-[460px] shadow-2xl z-50 overflow-auto" data-testid="entity-history-drawer">
          <HistoryTimeline
            projectId={projectId}
            entityType="extraction_entity"
            entityId={historyFor.id}
            onClose={() => setHistoryFor(null)}
          />
        </Glass>
      ) : null}
    </Glass>
  );
}
