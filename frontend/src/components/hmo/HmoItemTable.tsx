import {useCallback, useEffect, useMemo, useState} from "react";

import type {HmoStudioItem} from "@/api/hmoStudioItems";
import {ColumnFilterPopup} from "@/components/extraction/ColumnFilterPopup";
import {AiVerdictPill} from "@/components/extraction/AiVerdictPill";
import {HmoItemMappingBadge} from "@/components/hmo/HmoItemMappingBadge";
import {HmoItemShaclBadge} from "@/components/hmo/HmoItemShaclBadge";
import {HmoItemUploadOutcomeBadge} from "@/components/hmo/HmoItemUploadOutcomeBadge";
import {useReportDerivedIds} from "@/hooks/useReportDerivedIds";

const PAGE_SIZE = 25;

type ColKey = "class_qid" | "status" | "upload_outcome" | "validation" | "ai_verdict" | "approved";

function itemLabel(item: HmoStudioItem): string {
  return item.labels?.en || item.labels?.he || item.local_id;
}

function cellFilterValues(item: HmoStudioItem, col: ColKey): string[] {
  if (col === "validation") {
    const n = item.shacl_issues?.length ?? 0;
    return [n === 0 ? "ok" : (item.shacl_issues.some((i) => i.severity === "Error") ? "error" : "warn")];
  }
  if (col === "ai_verdict") return [item.ai_verdict?.overall ?? "unknown"];
  if (col === "upload_outcome") return [item.upload_outcome ?? "never"];
  if (col === "approved") {
    if (item.approved === true) return ["approved"];
    if (item.approved === false) return ["rejected"];
    return ["pending"];
  }
  const v = item[col];
  return v == null ? [] : [String(v)];
}

export interface HmoItemTableProps {
  items: HmoStudioItem[];
  onOpenItem: (item: HmoStudioItem) => void;
  onFilteredChange?: (ids: string[]) => void;
  onToggleApproved?: (item: HmoStudioItem, next: boolean) => void;
}

export function HmoItemTable({
  items,
  onOpenItem,
  onFilteredChange,
  onToggleApproved,
}: HmoItemTableProps) {
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<"label" | "status" | "local_id">("label");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(1);
  const [colFilters, setColFilters] = useState<Partial<Record<ColKey, Set<string>>>>({});
  const [popup, setPopup] = useState<{col: ColKey; x: number; y: number} | null>(null);

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

  const sorted = useMemo(() => {
    const copy = [...filtered];
    copy.sort((a, b) => {
      let av = "";
      let bv = "";
      if (sortKey === "label") {
        av = itemLabel(a);
        bv = itemLabel(b);
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
        placeholder="Search label, local ID, source URI…"
        className="input-glass text-sm w-full max-w-md"
        data-testid="hmo-item-search"
      />

      <div className="overflow-x-auto border border-white/5 rounded-lg">
        <table className="w-full text-sm" data-testid="hmo-item-table">
          <thead className="bg-white/3 text-xs uppercase muted tracking-wider">
            <tr>
              {([
                ["label", "Label", true],
                ["local_id", "Local ID", true],
                ["class_qid", "Class", false],
                ["source_uri", "Source URI", false],
                ["status", "On wiki?", false],
                ["wikibase_id", "QID", false],
                ["upload_outcome", "Last push", false],
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
                <td className="px-3 py-2 font-mono text-xs">{item.local_id}</td>
                <td className="px-3 py-2 font-mono text-xs">{item.class_qid}</td>
                <td className="px-3 py-2 text-xs truncate max-w-[200px]" title={item.source_uri}>{item.source_uri}</td>
                <td className="px-3 py-2"><HmoItemMappingBadge status={item.status} /></td>
                <td className="px-3 py-2 font-mono text-xs">{item.wikibase_id ?? "—"}</td>
                <td className="px-3 py-2" data-testid={`hmo-item-upload-outcome-${item.local_id}`}>
                  <HmoItemUploadOutcomeBadge
                    outcome={item.upload_outcome}
                    message={item.upload_message}
                    at={item.upload_at}
                    showDetail
                  />
                </td>
                <td className="px-3 py-2"><HmoItemShaclBadge issues={item.shacl_issues ?? []} /></td>
                <td className="px-3 py-2"><AiVerdictPill verdict={item.ai_verdict} /></td>
                <td className="px-3 py-2">
                  <input
                    type="checkbox"
                    checked={item.approved === true}
                    onChange={(e) => onToggleApproved?.(item, e.target.checked)}
                    data-testid={`hmo-item-approved-${item.local_id}`}
                  />
                </td>
                <td className="px-3 py-2">
                  <button type="button" className="button-ghost text-xs" onClick={() => onOpenItem(item)}>
                    Open
                  </button>
                </td>
              </tr>
            ))}
            {pageItems.length === 0 && (
              <tr>
                <td colSpan={11} className="px-3 py-6 text-center muted">No items match.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

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
