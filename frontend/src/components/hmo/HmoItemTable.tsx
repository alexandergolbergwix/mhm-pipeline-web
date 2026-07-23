import {useCallback, useEffect, useMemo, useState} from "react";

import type {HmoStudioItem} from "@/api/hmoStudioItems";
import {ColumnFilterPopup} from "@/components/extraction/ColumnFilterPopup";
import {HmoItemAiVerdictBadge} from "@/components/hmo/HmoItemAiVerdictBadge";
import {HmoItemDataStatusBadge} from "@/components/hmo/HmoItemDataStatusBadge";
import {HmoItemShaclBadge} from "@/components/hmo/HmoItemShaclBadge";
import {HmoItemUploadOutcomeBadge} from "@/components/hmo/HmoItemUploadOutcomeBadge";
import {CuratorTableScroll} from "@/components/CuratorTableScroll";
import {useReportDerivedIds} from "@/hooks/useReportDerivedIds";

import {resolveHmoItemDataStatus} from "@/utils/hmoItemDataStatus";

const PAGE_SIZE = 25;
const HMO_WIKIBASE_BASE_URL = "https://mhm-hmo.wikibase.cloud";

type ColKey = "type" | "data_status" | "upload_outcome" | "validation" | "ai_verdict" | "approved" | "class_qid" | "source_uri" | "wikibase_id" | "authority";

function itemLabel(item: HmoStudioItem): string {
  return item.labels?.en || item.labels?.he || item.local_id;
}

function authorityLinks(item: HmoStudioItem): string[] {
  const links = new Set<string>();
  for (const claim of item.claims ?? []) {
    if (typeof claim.value !== "string") continue;
    if (claim.value.includes("wikidata.org/entity/Q") || claim.value.includes("viaf.org/viaf/")) {
      links.add(claim.value);
    }
  }
  return [...links];
}


function enrichmentSummary(item: HmoStudioItem): Array<{kind: string; count: number}> {
  const counts = new Map<string, number>();
  for (const evidence of item.authority_evidence ?? []) {
    if (!evidence.accepted) continue;
    const kind = evidence.kind || evidence.source || "authority";
    counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }
  for (const link of authorityLinks(item)) {
    const kind = link.includes("wikidata.org") ? "Wikidata" : link.includes("nli.org.il") ? "Mazal/NLI" : "VIAF";
    if (!counts.has(kind)) counts.set(kind, 1);
  }
  return [...counts.entries()].map(([kind, count]) => ({kind, count}));
}

function cellFilterValues(item: HmoStudioItem, col: ColKey): string[] {
  if (col === "type") return [item.class_qid];
  if (col === "validation") {
    if (item.has_blocking_shacl) return ["blocked"];
    const n = item.shacl_issues?.length ?? 0;
    return [n === 0 ? "ok" : (item.shacl_issues.some((i) => i.severity === "Violation" || i.severity === "Error") ? "error" : "warn")];
  }
  if (col === "ai_verdict") return [item.ai_verdict?.overall ?? "unknown"];
  if (col === "data_status") return [resolveHmoItemDataStatus(item)];
  if (col === "upload_outcome") return [item.upload_outcome ?? "never"];
  if (col === "approved") {
    if (item.approved === true) return ["approved"];
    if (item.approved === false) return ["rejected"];
    return ["pending"];
  }
  const v = col === "authority" ? enrichmentSummary(item).map((entry) => entry.kind) : item[col];
  if (Array.isArray(v)) return v;
  return v == null ? [] : [String(v)];
}

export interface HmoItemTableProps {
  items: HmoStudioItem[];
  onOpenItem: (item: HmoStudioItem) => void;
  onFilteredChange?: (ids: string[]) => void;
  onToggleApproved?: (item: HmoStudioItem, next: boolean | null) => void;
}

export function HmoItemTable({
  items,
  onOpenItem,
  onFilteredChange,
  onToggleApproved,
}: HmoItemTableProps) {
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<"label" | "local_id" | "data_status">("label");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(1);
  const [colFilters, setColFilters] = useState<Partial<Record<ColKey, Set<string>>>>({});
  const [popup, setPopup] = useState<{col: ColKey; x: number; y: number} | null>(null);
  const [showTechnical, setShowTechnical] = useState(false);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((item) => {
      if (q) {
        const hay = `${item.local_id} ${item.source_uri} ${itemLabel(item)}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      for (const [col, wanted] of Object.entries(colFilters) as [ColKey, Set<string>][]) {
        if (!wanted?.size) continue;
        const vals = cellFilterValues(item, col);
        if (!vals.some((v) => wanted.has(v))) return false;
      }
      return true;
    });
  }, [items, search, colFilters]);

  const activeFilters = Object.entries(colFilters).flatMap(([col, values]) =>
    [...(values ?? [])].map((value) => ({col: col as ColKey, value})),
  );

  const removeFilter = useCallback((col: ColKey, value: string) => {
    setColFilters((prev) => {
      const next = new Set(prev[col] ?? []);
      next.delete(value);
      return {...prev, [col]: next};
    });
  }, []);

  const sorted = useMemo(() => {
    const copy = [...filtered];
    copy.sort((a, b) => {
      let av = "";
      let bv = "";
      if (sortKey === "label") {
        av = itemLabel(a);
        bv = itemLabel(b);
      } else if (sortKey === "data_status") {
        av = resolveHmoItemDataStatus(a);
        bv = resolveHmoItemDataStatus(b);
      } else {
        av = String(a[sortKey] ?? "");
        bv = String(b[sortKey] ?? "");
      }
      const cmp = av.localeCompare(bv);
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [filtered, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const pageItems = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  useReportDerivedIds(
    sorted.map((i) => i.local_id),
    onFilteredChange,
  );

  useEffect(() => {
    setPage(1);
  }, [search, colFilters, sortKey, sortDir]);

  const toggleSort = useCallback((key: typeof sortKey) => {
    setSortKey((prev) => {
      if (prev === key) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
        return prev;
      }
      setSortDir("asc");
      return key;
    });
  }, []);

  const distinctForCol = useCallback((col: ColKey): string[] => {
    const seen = new Set<string>();
    for (const item of items) {
      for (const v of cellFilterValues(item, col)) seen.add(v);
    }
    return [...seen].sort();
  }, [items]);

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
        {(["approved", "validation", "data_status", "ai_verdict"] as ColKey[]).map((col) => (
          <button
            key={col}
            type="button"
            className="button-ghost text-xs"
            onClick={(e) => setPopup({col, x: e.clientX, y: e.clientY})}
          >
            {col === "approved" ? "Review status" : col === "validation" ? "Data quality" : col === "data_status" ? "Publication status" : "AI review"}
          </button>
        ))}
        {activeFilters.map(({col, value}) => (
          <button key={`${col}-${value}`} type="button" className="rounded-full border border-white/15 px-2 py-1 text-xs" onClick={() => removeFilter(col, value)}>
            {value} ×
          </button>
        ))}
        {activeFilters.length > 0 && (
          <button type="button" className="button-ghost text-xs" onClick={() => setColFilters({})}>Clear all</button>
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
                      {colFilters[key as ColKey]?.size ? " ▾" : ""}
                    </button>
                  )}
                </th>
              ))}
              <th className="text-left px-3 py-2">Deep dive</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((item) => (
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
                  {enrichmentSummary(item).length ? (
                    <div className="flex flex-wrap gap-1" title="Accepted external authority evidence persisted on this HMO entity">
                      {enrichmentSummary(item).map(({kind, count}) => (
                        <span key={kind} className="rounded border border-emerald-400/30 px-1.5 py-0.5 text-emerald-200">{kind} ×{count}</span>
                      ))}
                    </div>
                  ) : <span className="muted">None</span>}
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
            {pageItems.length === 0 && (
              <tr>
                <td colSpan={showTechnical ? 14 : 8} className="px-3 py-6 text-center muted">No entries match.</td>
              </tr>
            )}
          </tbody>
        </table>
      </CuratorTableScroll>

      <div className="flex items-center justify-between text-xs muted">
        <span>{filtered.length} item{filtered.length === 1 ? "" : "s"}</span>
        <div className="flex items-center gap-2">
          <button type="button" className="button-ghost text-xs" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
          <span>{page} / {pageCount}</span>
          <button type="button" className="button-ghost text-xs" disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)}>Next</button>
        </div>
      </div>

      {popup && (
        <ColumnFilterPopup
          columnLabel={popup.col}
          values={distinctForCol(popup.col)}
          selected={colFilters[popup.col] ?? new Set()}
          x={popup.x}
          y={popup.y}
          onApply={(next) => {
            setColFilters((prev) => ({...prev, [popup.col]: next}));
            setPopup(null);
          }}
          onCancel={() => setPopup(null)}
        />
      )}
    </div>
  );
}
