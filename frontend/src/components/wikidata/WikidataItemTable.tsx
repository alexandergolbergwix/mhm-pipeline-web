import {useCallback, useEffect, useMemo, useState} from "react";

import type {StudioItem} from "@/api/wikidataStudio";
import {ColumnFilterPopup} from "@/components/extraction/ColumnFilterPopup";
import {CuratorTableScroll} from "@/components/CuratorTableScroll";
import {UploadOutcomeBadge} from "@/components/shared/UploadOutcomeBadge";
import {ItemValidatorBadge} from "@/components/wikidata/ItemValidatorBadge";
import {WikidataItemAiVerdictBadge} from "@/components/wikidata/WikidataItemAiVerdictBadge";
import {WikidataItemDataStatusBadge} from "@/components/wikidata/WikidataItemDataStatusBadge";
import {useReportDerivedIds} from "@/hooks/useReportDerivedIds";
import {resolveWikidataItemDataStatus} from "@/utils/wikidataItemDataStatus";

const PAGE_SIZE = 25;

type ColKey = "entity_type" | "data_status" | "upload_outcome" | "validation" | "ai_verdict" | "approved";

function itemLabel(item: StudioItem): string {
  const l = item.labels ?? {};
  return l.en || l.he || Object.values(l)[0] || item.local_id || "";
}

function cellFilterValues(item: StudioItem, col: ColKey): string[] {
  if (col === "validation") {
    const issues = item.validation_issues ?? [];
    if (issues.length === 0) return ["ok"];
    if (issues.some((i) => i.severity === "error")) return ["error"];
    return ["warn"];
  }
  if (col === "ai_verdict") return [item.ai_verdict?.overall ?? "not verified"];
  if (col === "data_status") return [resolveWikidataItemDataStatus(item)];
  if (col === "upload_outcome") return [item.upload_outcome ?? "never tried"];
  if (col === "approved") {
    if (item.approved === true) return ["approved"];
    if (item.approved === false) return ["rejected"];
    return ["pending"];
  }
  const v = item[col];
  return v == null ? [] : [String(v)];
}

export interface WikidataItemTableProps {
  items: StudioItem[];
  onOpenItem: (item: StudioItem) => void;
  onFilteredChange?: (ids: string[]) => void;
  onToggleApproved?: (item: StudioItem, next: boolean) => void;
  /** Local ids currently in an active AI-verify scope (show judging pill). */
  judgingIds?: ReadonlySet<string>;
}

export function WikidataItemTable({
  items,
  onOpenItem,
  onFilteredChange,
  onToggleApproved,
  judgingIds,
}: WikidataItemTableProps) {
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<"label" | "local_id" | "data_status">("label");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(1);
  const [colFilters, setColFilters] = useState<Partial<Record<ColKey, Set<string>>>>({});
  const [popup, setPopup] = useState<{col: ColKey; x: number; y: number} | null>(null);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((item) => {
      if (q) {
        const hay = `${item.local_id ?? ""} ${itemLabel(item)} ${item.entity_type ?? ""}`.toLowerCase();
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

  const sorted = useMemo(() => {
    const copy = [...filtered];
    copy.sort((a, b) => {
      let av = "";
      let bv = "";
      if (sortKey === "label") {
        av = itemLabel(a);
        bv = itemLabel(b);
      } else if (sortKey === "data_status") {
        av = resolveWikidataItemDataStatus(a);
        bv = resolveWikidataItemDataStatus(b);
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
    sorted.map((i) => i.local_id).filter((id): id is string => Boolean(id)),
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

  const countsForCol = useCallback((col: ColKey): Record<string, number> => {
    const counts: Record<string, number> = {};
    for (const item of items) {
      for (const v of cellFilterValues(item, col)) {
        counts[v] = (counts[v] ?? 0) + 1;
      }
    }
    return counts;
  }, [items]);

  return (
    <div className="space-y-3">
      <input
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search label, local ID, entity type…"
        className="input-glass text-sm w-full max-w-md"
        data-testid="wikidata-item-search"
      />

      <CuratorTableScroll data-testid="wikidata-item-table-scroll">
        <table className="w-full text-sm" data-testid="wikidata-item-table">
          <thead className="bg-white/3 text-xs uppercase muted tracking-wider sticky top-0 z-10 table-head">
            <tr>
              {([
                ["label", "Label", true],
                ["local_id", "Local ID", true],
                ["entity_type", "Entity type", false],
                ["data_status", "Data status", false],
                ["existing_qid", "QID", false],
                ["upload_outcome", "Last upload", false],
                ["validation", "Validation", false],
                ["ai_verdict", "AI verdict", false],
                ["approved", "Approved", false],
              ] as const).map(([key, label, sortable]) => (
                <th key={key} className="text-left px-3 py-2">
                  {sortable ? (
                    <button type="button" className="hover:text-ink" onClick={() => toggleSort(key === "label" ? "label" : "local_id")}>
                      {label}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="hover:text-ink"
                      data-testid={`wikidata-item-col-${key}`}
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
            {pageItems.map((item) => {
              const id = item.local_id ?? "";
              return (
                <tr key={id} className="border-t border-white/5" data-testid={`wikidata-item-row-${id}`}>
                  <td className="px-3 py-2">{itemLabel(item)}</td>
                  <td className="px-3 py-2 font-mono text-xs">{id}</td>
                  <td className="px-3 py-2 kicker">{item.entity_type ?? "—"}</td>
                  <td className="px-3 py-2" data-testid={`wikidata-item-data-status-${id}`}>
                    <WikidataItemDataStatusBadge item={item} />
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{item.existing_qid ?? "—"}</td>
                  <td className="px-3 py-2" data-testid={`wikidata-item-upload-outcome-${id}`}>
                    <UploadOutcomeBadge
                      outcome={item.upload_outcome}
                      message={item.upload_message}
                      at={item.upload_at}
                      localId={id}
                      testIdPrefix="wikidata-item"
                      showDetail
                    />
                  </td>
                  <td className="px-3 py-2">
                    {(item.validation_issues ?? []).length > 0 ? (
                      <ItemValidatorBadge issues={item.validation_issues ?? []} localId={id} />
                    ) : (
                      <span className="muted text-xs">ok</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <WikidataItemAiVerdictBadge
                      verdict={item.ai_verdict}
                      localId={id}
                      judging={Boolean(judgingIds?.has(id) && !item.ai_verdict?.overall)}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={item.approved === true}
                      onChange={(e) => onToggleApproved?.(item, e.target.checked)}
                      data-testid={`wikidata-item-approved-${id}`}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <button type="button" className="button-ghost text-xs" onClick={() => onOpenItem(item)}>
                      Open
                    </button>
                  </td>
                </tr>
              );
            })}
            {pageItems.length === 0 && (
              <tr>
                <td colSpan={10} className="px-3 py-6 text-center muted">No items match.</td>
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
          valueCounts={countsForCol(popup.col)}
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
