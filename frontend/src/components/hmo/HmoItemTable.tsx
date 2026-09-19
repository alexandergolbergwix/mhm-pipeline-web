import {useState} from "react";

import type {HmoStudioItem} from "@/api/hmoStudioItems";
import {ColumnFilterPopup} from "@/components/extraction/ColumnFilterPopup";
import {HmoItemAiVerdictBadge} from "@/components/hmo/HmoItemAiVerdictBadge";
import {HmoItemDataStatusBadge} from "@/components/hmo/HmoItemDataStatusBadge";
import {HmoItemRuleBadge} from "@/components/hmo/HmoItemRuleBadge";
import {HmoItemShaclBadge} from "@/components/hmo/HmoItemShaclBadge";
import {HmoItemUploadOutcomeBadge} from "@/components/hmo/HmoItemUploadOutcomeBadge";
import {CuratorTableScroll} from "@/components/CuratorTableScroll";

const HMO_WIKIBASE_BASE_URL = "https://mhm-hmo.wikibase.cloud";

type ColKey = "type" | "data_status" | "upload_outcome" | "validation" | "ai_verdict" | "rule_verdict" | "approved" | "class_qid" | "source_uri" | "wikibase_id" | "authority";

export type HmoItemSortKey = "label" | "local_id";

export interface HmoItemTableQuery {
  search: string;
  sortKey: HmoItemSortKey;
  sortDir: "asc" | "desc";
  colFilters: Partial<Record<ColKey, string[]>>;
}

export interface HmoItemTableProps {
  /** Current page rows only — the server owns filtering, sorting, paging. */
  items: HmoStudioItem[];
  total: number;
  page: number;
  pageCount: number;
  /** Distinct values per filterable column, computed server-side. */
  facets: Record<string, Record<string, number>>;
  query: HmoItemTableQuery;
  onQueryChange: (query: HmoItemTableQuery) => void;
  onPageChange: (page: number) => void;
  onOpenItem: (item: HmoStudioItem) => void;
  onToggleApproved?: (item: HmoStudioItem, next: boolean | null) => void;
}

function itemLabel(item: HmoStudioItem): string {
  return item.labels?.en || item.labels?.he || item.local_id;
}

export function HmoItemTable({
  items,
  total,
  page,
  pageCount,
  facets,
  query,
  onQueryChange,
  onPageChange,
  onOpenItem,
  onToggleApproved,
}: HmoItemTableProps) {
  const {search, sortKey, sortDir, colFilters} = query;
  const [popup, setPopup] = useState<{col: ColKey; x: number; y: number} | null>(null);
  const [showTechnical, setShowTechnical] = useState(false);

  const activeFilters = Object.entries(colFilters).flatMap(([col, values]) =>
    (values ?? []).map((value) => ({col: col as ColKey, value})),
  );

  const setSearch = (value: string) => onQueryChange({...query, search: value});
  const toggleSort = (key: HmoItemSortKey) => {
    if (sortKey === key) {
      onQueryChange({...query, sortDir: sortDir === "asc" ? "desc" : "asc"});
      return;
    }
    onQueryChange({...query, sortKey: key, sortDir: "asc"});
  };
  const removeFilter = (col: ColKey, value: string) => {
    onQueryChange({
      ...query,
      colFilters: {
        ...colFilters,
        [col]: (colFilters[col] ?? []).filter((v) => v !== value),
      },
    });
  };

  return (
    <div className="space-y-3">
      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search title, shelfmark, author, place, or record number…"
        className="input-glass text-sm w-full max-w-md"
        data-testid="hmo-item-search"
      />

      <div className="flex flex-wrap items-center gap-2" aria-label="Table filters">
        <span className="text-xs muted">Filters:</span>
        {(["approved", "validation", "data_status", "ai_verdict", "rule_verdict"] as ColKey[]).map((col) => (
          <button
            key={col}
            type="button"
            className="button-ghost text-xs"
            onClick={(e) => setPopup({col, x: e.clientX, y: e.clientY})}
          >
            {col === "approved" ? "Review status" : col === "validation" ? "Data quality" : col === "data_status" ? "Publication status" : col === "ai_verdict" ? "AI review" : "Rule check"}
          </button>
        ))}
        {activeFilters.map(({col, value}) => (
          <button key={`${col}-${value}`} type="button" className="rounded-full border border-white/15 px-2 py-1 text-xs" onClick={() => removeFilter(col, value)}>
            {value} ×
          </button>
        ))}
        {activeFilters.length > 0 && (
          <button type="button" className="button-ghost text-xs" onClick={() => onQueryChange({...query, colFilters: {}})}>Clear all</button>
        )}
        <button type="button" className="button-ghost text-xs" onClick={() => setShowTechnical((value) => !value)}>
          {showTechnical ? "Hide technical columns" : "Show technical columns"}
        </button>
      </div>

      <CuratorTableScroll data-testid="hmo-item-table-scroll">
        <table className="w-full text-sm" data-testid="hmo-item-table">
          <thead className="bg-white/3 text-xs uppercase muted tracking-wider sticky top-0 z-10 table-head">
            <tr>
              {([
                ["label", "Title / shelfmark", true],
                ["type", "Type", false],
                ["approved", "Review status", false],
                ["validation", "Data quality", false],
                ["data_status", "Publication status", false],
                ["ai_verdict", "AI review", false],
                ["rule_verdict", "Rule check", false],
                ...(showTechnical ? [
                  ["local_id", "Record ID", true],
                  ["class_qid", "Technical class", false],
                  ["source_uri", "Source URI", false],
                  ["wikibase_id", "HMO record ID", false],
                  ["authority", "External authority", false],
                  ["upload_outcome", "Last publication", false],
                ] : []),
              ] as const).map(([key, label, sortable]) => (
                <th key={String(key)} className="text-left px-3 py-2">
                  {sortable ? (
                    <button type="button" className="hover:text-ink" onClick={() => toggleSort(key === "label" ? "label" : "local_id")}>
                      {label}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="hover:text-ink"
                      data-testid={`hmo-item-col-${key}`}
                      onClick={(e) => setPopup({col: key as ColKey, x: e.clientX, y: e.clientY})}
                      onContextMenu={(e) => {
                        e.preventDefault();
                        setPopup({col: key as ColKey, x: e.clientX, y: e.clientY});
                      }}
                    >
                      {label}
                      {colFilters[key as ColKey]?.length ? " ▾" : ""}
                    </button>
                  )}
                </th>
              ))}
              <th className="text-left px-3 py-2">Deep dive</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.local_id} className="border-t border-white/5" data-testid={`hmo-item-row-${item.local_id}`}>
                <td className="px-3 py-2">{itemLabel(item)}</td>
                <td className="px-3 py-2">{item.class_qid}</td>
                <td className="px-3 py-2">
                  {item.approved === null ? "Pending review" : item.approved ? "Approved" : "Rejected"}
                </td>
                <td className="px-3 py-2">
                  <HmoItemShaclBadge issues={item.shacl_issues ?? []} localId={item.local_id} />
                </td>
                <td className="px-3 py-2"><HmoItemDataStatusBadge item={item} /></td>
                <td className="px-3 py-2"><HmoItemAiVerdictBadge verdict={item.ai_verdict} localId={item.local_id} /></td>
                <td className="px-3 py-2"><HmoItemRuleBadge item={item} /></td>
                {showTechnical && <td className="px-3 py-2 font-mono text-xs">{item.local_id}</td>}
                {showTechnical && <td className="px-3 py-2 font-mono text-xs">{item.class_qid}</td>}
                {showTechnical && <td className="px-3 py-2 text-xs truncate max-w-[200px]" title={item.source_uri}>
                  {item.wikibase_id ? (
                    <a
                      href={`${HMO_WIKIBASE_BASE_URL}/wiki/Item:${item.wikibase_id}`}
                      target="_blank"
                      rel="noreferrer"
                      className="underline"
                    >
                      Open Wikibase entity ↗
                    </a>
                  ) : item.source_uri}
                </td>}
                {showTechnical && <td className="px-3 py-2 font-mono text-xs" title="Project HMO record identifier">
                  {item.wikibase_id ?? "—"}
                </td>}
                {showTechnical && <td className="px-3 py-2 text-xs">
                  {(item.authority_evidence ?? []).some((e) => e.accepted)
                    ? <span className="rounded border border-emerald-400/30 px-1.5 py-0.5 text-emerald-200">enriched</span>
                    : <span className="muted">None</span>}
                </td>}
                {showTechnical && <td className="px-3 py-2" data-testid={`hmo-item-upload-outcome-${item.local_id}`}>
                  <HmoItemUploadOutcomeBadge
                    outcome={item.upload_outcome}
                    message={item.upload_message}
                    at={item.upload_at}
                    localId={item.local_id}
                    showDetail
                  />
                </td>}
                <td className="px-3 py-2">
                  <select
                    value={item.approved === null ? "pending" : item.approved ? "approved" : "rejected"}
                    onChange={(e) => onToggleApproved?.(item, e.target.value === "pending" ? null : e.target.value === "approved")}
                    aria-label={`Review status for ${itemLabel(item)}`}
                    data-testid={`hmo-item-approved-${item.local_id}`}
                    className="input-glass text-xs"
                  >
                    <option value="pending">Pending review</option>
                    <option value="approved">Approved</option>
                    <option value="rejected">Rejected</option>
                  </select>
                </td>
                <td className="px-3 py-2">
                  <button type="button" className="button-ghost text-xs" onClick={() => onOpenItem(item)}>
                    Review entry
                  </button>
                </td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={showTechnical ? 15 : 9} className="px-3 py-6 text-center muted">No entries match.</td>
              </tr>
            )}
          </tbody>
        </table>
      </CuratorTableScroll>

      <div className="flex items-center justify-between text-xs muted">
        <span>{total} item{total === 1 ? "" : "s"}</span>
        <div className="flex items-center gap-2">
          <button type="button" className="button-ghost text-xs" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>Prev</button>
          <span>{page} / {pageCount}</span>
          <button type="button" className="button-ghost text-xs" disabled={page >= pageCount} onClick={() => onPageChange(page + 1)}>Next</button>
        </div>
      </div>

      {popup && (
        <ColumnFilterPopup
          columnLabel={popup.col}
          values={Object.keys(facets[popup.col] ?? {}).sort()}
          valueCounts={facets[popup.col] ?? {}}
          selected={new Set(colFilters[popup.col] ?? [])}
          x={popup.x}
          y={popup.y}
          onApply={(next) => {
            onQueryChange({
              ...query,
              colFilters: {...colFilters, [popup.col]: [...next]},
            });
            setPopup(null);
          }}
          onCancel={() => setPopup(null)}
        />
      )}
    </div>
  );
}
