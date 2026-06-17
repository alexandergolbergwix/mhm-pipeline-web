/**
 * AuthorityTable — authority candidates review table.
 *
 * 9 columns matching the extraction EntityTable layout:
 *   Record · Entity · Role · Source · Conf. · Guards · AI verdict · Approved · Edit
 *
 * Features:
 *  - Per-column right-click filter popup (Rule 49 §E parity with EntityTable)
 *  - Free-text header search on Record + Entity columns
 *  - Sortable headers with custom orderings for Confidence and AI verdict
 *  - Guard-flags inline chips
 *  - ConfidenceBadge / VerdictBadge reused from MatchDetailDialog
 */

import { useEffect, useMemo, useState } from "react";

import type { AuthorityMatch, ExistsIn } from "@/api/runs";
import { ColumnFilterPopup } from "@/components/extraction/ColumnFilterPopup";
import { ConfidenceBadge, VerdictBadge } from "@/components/MatchDetailDialog";
import { HistoryTimeline } from "@/components/history/HistoryTimeline";

type ColumnKey =
  | "control_number"
  | "entity_text"
  | "role"
  | "source"
  | "confidence"
  | "exists_in"
  | "guard_flags"
  | "ai_verdict"
  | "approved"
  | "edit";

interface ColDef {
  key:        ColumnKey;
  label:      string;
  width?:     string;
  sortable:   boolean;
  filterable: boolean;
  textHeader: boolean;
}

const COLS: ColDef[] = [
  { key: "control_number", label: "Record",     width: "100px",   sortable: true,  filterable: false, textHeader: true  },
  { key: "entity_text",    label: "Entity",     width: undefined, sortable: true,  filterable: false, textHeader: true  },
  { key: "role",           label: "Role",       width: "120px",   sortable: true,  filterable: true,  textHeader: false },
  { key: "source",         label: "Source",     width: "170px",   sortable: true,  filterable: true,  textHeader: false },
  { key: "confidence",     label: "Conf.",      width: "90px",    sortable: true,  filterable: true,  textHeader: false },
  { key: "exists_in",      label: "MARC",       width: "90px",    sortable: true,  filterable: true,  textHeader: false },
  { key: "guard_flags",    label: "Guards",     width: "160px",   sortable: false, filterable: true,  textHeader: false },
  { key: "ai_verdict",     label: "AI verdict", width: "120px",   sortable: true,  filterable: true,  textHeader: false },
  { key: "approved",       label: "Approved",   width: "80px",    sortable: true,  filterable: true,  textHeader: false },
  { key: "edit",           label: "",           width: "96px",    sortable: false, filterable: false, textHeader: false },
];

type SortDir = "asc" | "desc";
const CONF_ORDER: Record<string, number> = { low: 0, medium: 1, high: 2 };
const VERD_ORDER: Record<string, number> = { fail: 0, partial: 1, abstain: 2, full: 3, pass: 3 };

// Returns all filterable values for a column on a single row.
// For multi-value columns (source, guard_flags) may return multiple elements.
function cellValues(m: AuthorityMatch, col: ColumnKey): string[] {
  if (col === "source") {
    const srcs = (m.payload?.sources as string[] | undefined) ?? [];
    return srcs.length > 0 ? srcs : [m.source].filter(Boolean);
  }
  if (col === "guard_flags") {
    return (m.payload?.guard_flags as string[] | undefined) ?? [];
  }
  if (col === "ai_verdict") {
    const v = m.payload?.ai_verdict as { overall?: string } | undefined;
    const ov = v?.overall ?? "";
    return ov ? [ov] : [];
  }
  if (col === "approved") {
    return [m.approved ? "approved" : "not approved"];
  }
  if (col === "exists_in") {
    const label = existsInLabel(m.exists_in?.status);
    return label ? [label] : ["—"];
  }
  const val = String((m as unknown as Record<string, unknown>)[col] ?? "");
  return val ? [val] : [];
}

// Returns a single string for sort comparison.
function cellSortKey(m: AuthorityMatch, col: ColumnKey): string {
  if (col === "source") {
    const srcs = (m.payload?.sources as string[] | undefined) ?? [];
    return srcs.join(",");
  }
  if (col === "guard_flags") {
    return String((m.payload?.guard_flags as string[] | undefined)?.length ?? 0);
  }
  if (col === "exists_in") {
    // Sort order: grounded (0) < wrong_field (1) < unknown (2) < novel (3) < absent (4)
    const ORDER: Record<string, number> = { grounded: 0, wrong_field: 1, unknown: 2, novel: 3 };
    return String(ORDER[m.exists_in?.status ?? ""] ?? 4);
  }
  return cellValues(m, col)[0] ?? "";
}

export interface AuthorityTableProps {
  matches:          AuthorityMatch[];
  runId:            string;
  projectId:        string;
  selectedIds:      Set<string>;
  onSelectToggle:   (id: string) => void;
  onApproveToggle:  (m: AuthorityMatch) => void;
  onOpenDrawer:     (m: AuthorityMatch) => void;
  onOpenEdit:       (m: AuthorityMatch) => void;
  onMatchChanged:   (updated: AuthorityMatch) => void;
  onFilteredChange?: (ids: string[]) => void;
}

// Normalise entity text for grouping (strip niqqud, lowercase).
function normalizeEntityText(t: string): string {
  return t
    .replace(/[\u0591-\u05C7]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

// A grouped row: primary match + zero or more secondary matches sharing
// (control_number, normalizedText).
export interface GroupedMatch {
  primary: AuthorityMatch;
  alts:    AuthorityMatch[];
}

export function AuthorityTable({
  matches,
  runId: _runId,
  projectId,
  selectedIds,
  onSelectToggle,
  onApproveToggle,
  onOpenDrawer,
  onOpenEdit,
  onFilteredChange,
}: AuthorityTableProps) {
  const [sort, setSort] = useState<{ key: ColumnKey; dir: SortDir } | null>(null);
  const [columnFilters, setColumnFilters] = useState<Record<string, Set<string>>>({});
  const [textFilters, setTextFilters] = useState<Partial<Record<"control_number" | "entity_text", string>>>({});
  const [popup, setPopup] = useState<{ col: ColumnKey; x: number; y: number } | null>(null);
  const [historyFor, setHistoryFor] = useState<{ id: string } | null>(null);
  const [groupDuplicates, setGroupDuplicates] = useState(true);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  // Collect distinct values per filterable column for the popup.
  const distinctValues = useMemo<Record<string, string[]>>(() => {
    const acc: Record<string, Set<string>> = {};
    for (const m of matches) {
      for (const col of COLS) {
        if (!col.filterable) continue;
        acc[col.key] = acc[col.key] ?? new Set();
        for (const v of cellValues(m, col.key)) {
          if (v) acc[col.key].add(v);
        }
      }
    }
    const out: Record<string, string[]> = {};
    for (const k of Object.keys(acc)) out[k] = Array.from(acc[k]).sort();
    return out;
  }, [matches]);

  // Filtering + sorting pipeline.
  const display = useMemo<AuthorityMatch[]>(() => {
    let out = [...matches];

    // Column popup filters — OR within column, AND across columns.
    for (const [col, selected] of Object.entries(columnFilters)) {
      if (selected.size === 0) continue;
      out = out.filter((m) =>
        cellValues(m, col as ColumnKey).some((v) => selected.has(v)),
      );
    }

    // Free-text header filters.
    const cnFilter = textFilters.control_number?.trim().toLowerCase();
    if (cnFilter) out = out.filter((m) => m.control_number.toLowerCase().includes(cnFilter));
    const etFilter = textFilters.entity_text?.trim().toLowerCase();
    if (etFilter) out = out.filter((m) => m.entity_text.toLowerCase().includes(etFilter));

    // Sort.
    if (sort) {
      const { key, dir } = sort;
      const mul = dir === "asc" ? 1 : -1;
      out = [...out].sort((a, b) => {
        let r = 0;
        if (key === "confidence") {
          r = (CONF_ORDER[a.confidence] ?? -1) - (CONF_ORDER[b.confidence] ?? -1);
        } else if (key === "ai_verdict") {
          const av = (a.payload?.ai_verdict as { overall?: string } | undefined)?.overall ?? "";
          const bv = (b.payload?.ai_verdict as { overall?: string } | undefined)?.overall ?? "";
          r = (VERD_ORDER[av] ?? -1) - (VERD_ORDER[bv] ?? -1);
        } else if (key === "source") {
          const ac = Number(a.payload?.source_count ?? 0);
          const bc = Number(b.payload?.source_count ?? 0);
          r = ac - bc;
          if (r === 0) r = cellSortKey(a, key).localeCompare(cellSortKey(b, key));
        } else if (key === "approved") {
          r = (a.approved ? 1 : 0) - (b.approved ? 1 : 0);
        } else {
          r = cellSortKey(a, key).localeCompare(cellSortKey(b, key), undefined,
            { sensitivity: "base", numeric: true });
        }
        return r * mul;
      });
    }

    return out;
  }, [matches, columnFilters, textFilters, sort]);

  // Grouped view: collapse rows with same (control_number, normalized entity_text)
  // into a single representative row (primary = highest-priority role), with alt
  // rows accessible via expansion.
  const grouped = useMemo<GroupedMatch[]>(() => {
    if (!groupDuplicates) {
      return display.map((m) => ({primary: m, alts: []}));
    }
    const groups = new Map<string, GroupedMatch>();
    const _ROLE_RANK: Record<string, number> = {
      author: 4, scribe: 4, translator: 4, editor: 4,
      contributor: 3, subject: 2, production_place: 1, place: 0,
    };
    for (const m of display) {
      const gKey = `${m.control_number}__${normalizeEntityText(m.entity_text)}`;
      const existing = groups.get(gKey);
      if (!existing) {
        groups.set(gKey, {primary: m, alts: []});
      } else {
        const existRank = _ROLE_RANK[existing.primary.role?.toLowerCase() ?? ""] ?? 2;
        const newRank = _ROLE_RANK[m.role?.toLowerCase() ?? ""] ?? 2;
        if (newRank > existRank) {
          existing.alts.unshift(existing.primary);
          existing.primary = m;
        } else {
          existing.alts.push(m);
        }
      }
    }
    return Array.from(groups.values());
  }, [display, groupDuplicates]);

  useEffect(() => {
    // Report filtered IDs as all primary + all alt IDs so bulk actions work.
    const allIds = grouped.flatMap((g) => [g.primary.id, ...g.alts.map((a) => a.id)]);
    onFilteredChange?.(allIds);
  }, [grouped, onFilteredChange]);

  function toggleSort(col: ColumnKey) {
    setSort((prev) => {
      if (prev?.key !== col) return { key: col, dir: "asc" };
      if (prev.dir === "asc") return { key: col, dir: "desc" };
      return null;
    });
  }

  function handleHeaderContext(e: React.MouseEvent, col: ColumnKey) {
    e.preventDefault();
    setPopup({ col, x: e.clientX, y: e.clientY });
  }

  function applyPopup(selected: Set<string>) {
    if (!popup) return;
    setColumnFilters((prev) => ({ ...prev, [popup.col]: selected }));
    setPopup(null);
  }

  const activeFilterCols = Object.entries(columnFilters)
    .filter(([, s]) => s.size > 0)
    .map(([k]) => k);

  const hasAnyTextFilter = Object.values(textFilters).some((v) => v?.trim());

  return (
    <>
      {/* Active filter chips */}
      {(activeFilterCols.length > 0 || hasAnyTextFilter) && (
        <div className="flex flex-wrap items-center gap-2 pb-1">
          {Object.entries(textFilters).map(([col, val]) => {
            if (!val?.trim()) return null;
            const colDef = COLS.find((c) => c.key === col);
            return (
              <span key={col}
                    className="glass-pill flex items-center gap-1.5 px-2 py-0.5 text-xs text-biu-sky">
                {colDef?.label || col}: &ldquo;{val}&rdquo;
                <button type="button"
                        onClick={() => setTextFilters((p) => ({ ...p, [col]: "" }))}
                        className="ml-0.5 text-muted hover:text-ink">✕</button>
              </span>
            );
          })}
          {activeFilterCols.map((col) => {
            const colDef = COLS.find((c) => c.key === col);
            const selected = columnFilters[col]!;
            return (
              <span key={col}
                    className="glass-pill flex items-center gap-1.5 px-2 py-0.5 text-xs text-biu-sky">
                {colDef?.label || col}: {Array.from(selected).join(", ")}
                <button type="button"
                        onClick={() => setColumnFilters((p) => { const n = { ...p }; delete n[col]; return n; })}
                        className="ml-0.5 text-muted hover:text-ink">✕</button>
              </span>
            );
          })}
          <button type="button"
                  onClick={() => { setColumnFilters({}); setTextFilters({}); }}
                  className="text-xs text-biu-sky hover:underline">
            Clear all
          </button>
        </div>
      )}

      {/* Group-duplicates toggle */}
      <div className="flex items-center gap-2 py-1 text-xs text-muted">
        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={groupDuplicates}
            onChange={(e) => setGroupDuplicates(e.target.checked)}
            className="accent-biu-sky"
          />
          Group duplicates
        </label>
        {groupDuplicates && (
          <span className="text-muted">
            ({grouped.length} groups · {display.length} rows)
          </span>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead className="muted text-left">
            <tr className="border-b border-white/5">
              <th className="py-2 pr-2 w-8"></th>
              {COLS.map((col) => {
                const isPopupActive = (columnFilters[col.key]?.size ?? 0) > 0;
                const isTextActive = col.textHeader &&
                  !!(textFilters[col.key as "control_number" | "entity_text"]?.trim());
                const isActive = isPopupActive || isTextActive;
                return (
                  <th
                    key={col.key}
                    style={col.width ? { width: col.width } : undefined}
                    className="py-2 pr-3 select-none align-top"
                    onContextMenu={col.filterable ? (e) => handleHeaderContext(e, col.key) : undefined}
                  >
                    {col.key === "edit" ? null : (
                      <div className="flex flex-col gap-0.5">
                        <div className="flex items-center gap-0.5">
                          {col.sortable ? (
                            <button
                              type="button"
                              onClick={() => toggleSort(col.key)}
                              className={`inline-flex items-center gap-1 hover:text-ink transition ${isActive ? "text-biu-sky" : ""}`}
                            >
                              <span>{col.label}</span>
                              <SortGlyph active={sort?.key === col.key} dir={sort?.dir ?? "asc"} />
                            </button>
                          ) : (
                            <span className={`cursor-default ${isActive ? "text-biu-sky" : ""}`}>
                              {col.label}
                            </span>
                          )}
                          {col.filterable && (
                            <button
                              type="button"
                              title={`Filter ${col.label} (right-click header)`}
                              onClick={(e) => handleHeaderContext(e, col.key)}
                              className={`text-[10px] hover:text-ink transition ${isPopupActive ? "text-biu-sky" : "muted"}`}
                            >▾</button>
                          )}
                        </div>
                        {col.textHeader && (
                          <input
                            type="text"
                            placeholder={`Search…`}
                            value={textFilters[col.key as "control_number" | "entity_text"] ?? ""}
                            onChange={(e) =>
                              setTextFilters((p) => ({ ...p, [col.key]: e.target.value }))
                            }
                            onClick={(e) => e.stopPropagation()}
                            className="input-glass h-6 w-full text-[11px] font-normal"
                          />
                        )}
                      </div>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {grouped.map(({primary: m, alts}) => {
              const p = (m.payload ?? {}) as Record<string, unknown>;
              const ai = (p.ai_verdict ?? null) as null | { overall: string };
              const guards = (p.guard_flags as string[] | undefined) ?? [];
              const sourceCount = Number(p.source_count ?? 0);
              const sources = (p.sources as string[] | undefined) ?? [];
              const gKey = `${m.control_number}__${normalizeEntityText(m.entity_text)}`;
              const isExpanded = expandedGroups.has(gKey);
              const hasAlts = alts.length > 0;
              return (
                <>
                  <tr
                    key={m.id}
                    data-testid={`authority-row-${m.id}`}
                    className="border-b border-white/5 hover:bg-white/[0.03] transition cursor-pointer"
                    onClick={() => onOpenDrawer(m)}
                  >
                    {/* Select checkbox */}
                    <td className="py-2 pr-2" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={selectedIds.has(m.id)}
                        onChange={() => onSelectToggle(m.id)}
                      />
                    </td>

                    {/* Record */}
                    <td className="py-2 pr-3 font-mono text-xs text-biu-sky whitespace-nowrap"
                        onClick={(e) => e.stopPropagation()}>
                      {m.control_number}
                    </td>

                    {/* Entity */}
                    <td className="py-2 pr-3 max-w-[260px]">
                      <span className="truncate block">{m.entity_text}</span>
                      {hasAlts && groupDuplicates && (
                        <button
                          type="button"
                          data-testid={`expand-group-${gKey}`}
                          className="text-[10px] text-biu-sky hover:underline mt-0.5"
                          onClick={(e) => {
                            e.stopPropagation();
                            setExpandedGroups((prev) => {
                              const n = new Set(prev);
                              if (n.has(gKey)) n.delete(gKey); else n.add(gKey);
                              return n;
                            });
                          }}
                        >
                          {isExpanded ? "▲ hide" : `▼ +${alts.length} role${alts.length > 1 ? "s" : ""}`}
                        </button>
                      )}
                    </td>

                    {/* Role — show role chips when grouped */}
                    <td className="py-2 pr-3">
                      <span className="kicker">{m.role || "—"}</span>
                      {hasAlts && groupDuplicates && !isExpanded && (
                        <span className="ml-1 inline-flex flex-wrap gap-0.5">
                          {alts.map((a) => (
                            <span key={a.id} className="glass-pill px-1 py-[1px] text-[10px] text-muted"
                                  title={a.entity_text}>
                              {a.role || "—"}
                            </span>
                          ))}
                        </span>
                      )}
                    </td>

                    {/* Source */}
                    <td className="py-2 pr-3">
                      <span className="inline-flex items-center gap-1 flex-wrap">
                        {sources.length > 0
                          ? sources.map((s) => (
                              <span key={s}
                                    className="glass-pill px-1.5 py-[1px] text-[10px] uppercase tracking-wider whitespace-nowrap">
                                {s}
                              </span>
                            ))
                          : <span className="muted text-xs italic">—</span>}
                        {sourceCount >= 2 && (
                          <span className="text-biu-sky text-[10px]" title="Cross-source agreement">
                            ✓×{sourceCount}
                          </span>
                        )}
                      </span>
                    </td>

                    {/* Confidence */}
                    <td className="py-2 pr-3">
                      <ConfidenceBadge confidence={m.confidence} />
                    </td>

                    {/* MARC novelty */}
                    <td className="py-2 pr-3">
                      <ExistsInBadge ei={m.exists_in} />
                    </td>

                    {/* Guards */}
                    <td className="py-2 pr-3">
                      {guards.length > 0 ? (
                        <span className="inline-flex flex-wrap gap-1">
                          {guards.map((g) => (
                            <span key={g}
                                  className="glass-pill px-1.5 py-[1px] text-[10px] text-red-300 whitespace-nowrap"
                                  title={guardExplain(g)}>
                              ⚠ {g}
                            </span>
                          ))}
                        </span>
                      ) : (
                        <span className="muted text-xs">—</span>
                      )}
                    </td>

                    {/* AI verdict */}
                    <td className="py-2 pr-3">
                      {ai
                        ? <VerdictBadge overall={ai.overall} />
                        : <span className="muted text-xs italic">—</span>}
                    </td>

                    {/* Approved */}
                    <td className="py-2 pr-3" onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={m.approved} onChange={() => onApproveToggle(m)} />
                    </td>

                    {/* Edit actions */}
                    <td className="py-2 pr-1 text-right" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => onOpenDrawer(m)}
                          title="View details"
                          className="button-ghost h-7 px-2 text-xs"
                        >👁</button>
                        <button
                          type="button"
                          onClick={() => onOpenEdit(m)}
                          data-testid={`match-edit-${m.id}`}
                          title="Edit match fields"
                          className="button-ghost h-7 px-2 text-xs"
                        >✎</button>
                        <button
                          type="button"
                          data-testid={`history-button-${m.id}`}
                          onClick={() => setHistoryFor({ id: String(m.id) })}
                          aria-label="View edit history"
                          title="View edit history"
                          className="button-ghost h-7 px-2 text-xs"
                        >📜</button>
                      </div>
                    </td>
                  </tr>
                  {/* Expanded alt rows */}
                  {isExpanded && alts.map((alt) => {
                    const ap = (alt.payload ?? {}) as Record<string, unknown>;
                    const aai = (ap.ai_verdict ?? null) as null | { overall: string };
                    return (
                      <tr
                        key={alt.id}
                        data-testid={`authority-alt-row-${alt.id}`}
                        className="border-b border-white/5 bg-white/[0.015] text-muted cursor-pointer"
                        onClick={() => onOpenDrawer(alt)}
                      >
                        <td className="py-1.5 pr-2 pl-4" onClick={(e) => e.stopPropagation()}>
                          <input type="checkbox" checked={selectedIds.has(alt.id)}
                                 onChange={() => onSelectToggle(alt.id)} />
                        </td>
                        <td className="py-1.5 pr-3 font-mono text-[11px] text-biu-sky/60">{alt.control_number}</td>
                        <td className="py-1.5 pr-3 text-xs text-muted/70 max-w-[260px] truncate pl-4">
                          ↳ {alt.entity_text}
                        </td>
                        <td className="py-1.5 pr-3">
                          <span className="kicker text-muted">{alt.role || "—"}</span>
                        </td>
                        <td className="py-1.5 pr-3 text-xs text-muted">
                          {((ap.sources as string[] | undefined) ?? []).join(", ") || alt.source}
                        </td>
                        <td className="py-1.5 pr-3">
                          <ConfidenceBadge confidence={alt.confidence} />
                        </td>
                        <td className="py-1.5 pr-3">
                          <ExistsInBadge ei={alt.exists_in} />
                        </td>
                        <td className="py-1.5 pr-3 text-xs text-muted">
                          {((ap.guard_flags as string[] | undefined) ?? []).join(", ") || "—"}
                        </td>
                        <td className="py-1.5 pr-3">
                          {aai ? <VerdictBadge overall={aai.overall} /> : <span className="muted text-xs italic">—</span>}
                        </td>
                        <td className="py-1.5 pr-3" onClick={(e) => e.stopPropagation()}>
                          <input type="checkbox" checked={alt.approved} onChange={() => onApproveToggle(alt)} />
                        </td>
                        <td className="py-1.5 pr-1 text-right" onClick={(e) => e.stopPropagation()}>
                          <button type="button" onClick={() => onOpenEdit(alt)}
                                  data-testid={`match-edit-${alt.id}`}
                                  title="Edit" className="button-ghost h-7 px-2 text-xs">✎</button>
                        </td>
                      </tr>
                    );
                  })}
                </>
              );
            })}
            {grouped.length === 0 && (
              <tr>
                <td colSpan={COLS.length + 1} className="py-6 text-center muted">
                  No rows match this filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Column filter popup */}
      {popup && (
        <ColumnFilterPopup
          columnLabel={COLS.find((c) => c.key === popup.col)?.label ?? popup.col}
          values={distinctValues[popup.col] ?? []}
          selected={columnFilters[popup.col] ?? new Set()}
          x={popup.x}
          y={popup.y}
          onApply={applyPopup}
          onCancel={() => setPopup(null)}
        />
      )}

      {/* Inline history drawer */}
      {historyFor && (
        <aside
          data-testid="authority-table-history-drawer"
          className="fixed right-0 top-0 h-full w-[460px] glass shadow-2xl z-50 overflow-auto"
        >
          <HistoryTimeline
            projectId={projectId}
            entityType="authority_match"
            entityId={historyFor.id}
            onClose={() => setHistoryFor(null)}
          />
        </aside>
      )}
    </>
  );
}


// ── Helpers ─────────────────────────────────────────────────────────────

const _EXISTS_IN_CFG: Record<string, { label: string; cls: string }> = {
  grounded:    { label: "in MARC",  cls: "text-green-400 bg-green-400/10 border-green-400/30" },
  novel:       { label: "novel",    cls: "text-sky-400 bg-sky-400/10 border-sky-400/30" },
  wrong_field: { label: "mismatch", cls: "text-amber-400 bg-amber-400/10 border-amber-400/30" },
  unknown:     { label: "?",        cls: "text-slate-500 bg-slate-500/10 border-slate-500/30" },
};

function existsInLabel(status?: string | null): string | null {
  if (!status) return null;
  return _EXISTS_IN_CFG[status]?.label ?? "?";
}

function ExistsInBadge({ ei }: { ei?: ExistsIn | null }) {
  if (!ei?.status) return <span className="muted text-xs">—</span>;
  const cfg = _EXISTS_IN_CFG[ei.status] ?? _EXISTS_IN_CFG.unknown;
  const title = [ei.note, ei.fields?.join(", ")].filter(Boolean).join(" · ");
  return (
    <span
      title={title || undefined}
      className={`inline-block text-xs px-1.5 py-0.5 rounded-md border font-medium ${cfg.cls}`}
    >
      {cfg.label}
    </span>
  );
}


function SortGlyph({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active)        return <span className="muted text-[9px]">⇅</span>;
  if (dir === "asc")  return <span className="text-biu-sky text-[10px]">↑</span>;
  return                    <span className="text-biu-sky text-[10px]">↓</span>;
}


const _GUARD_NOTES: Record<string, string> = {
  placeholder_name:       "Name is a generic placeholder (e.g. 'ben', 'bar') — resolved IDs were cleared.",
  short_name_homonym:     "Short or highly ambiguous name — many persons share this form.",
  cluster_collapse:       "Candidate collapses with a sibling that has stronger evidence.",
  nli_strict_skip_viaf:   "NLI authority conflicts with the VIAF record — VIAF link was dropped.",
  mazal_pair_collision:   "Mazal ID collides with a sibling candidate for a different person.",
  corporate_viaf_drop:    "Corporate/institution name matched a VIAF person record — VIAF link was dropped.",
  wikidata_crosscheck_fail: "Wikidata crosscheck found a conflicting identity for this candidate.",
};
function guardExplain(g: string): string {
  return _GUARD_NOTES[g] ?? "";
}
