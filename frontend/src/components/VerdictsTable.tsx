/**
 * VerdictsTable — research-grade rendering of eval-agent verdicts.
 *
 * Replaces the previous tiny "name + ✓/×" list that truncated the
 * agent's reasoning. A researcher writing a paper needs to cite the
 * judgement: model id, timestamp, cache key (determinism), full
 * reasoning, the MARC fields the agent grounded against
 * (provenance), and the matched authority record (verification
 * sources). Every column here corresponds to a piece of evidence
 * that survives into the final paper.
 *
 * One row per verdict; click the row to expand for full reasoning +
 * the ``exists_in`` provenance + the raw candidate JSON.
 *
 * The verdict event shape we consume (from eval-agent's
 * results.jsonl):
 *
 *     {
 *       schema_version: 1,
 *       judge_id: "gemini-3.5-flash",        ← model that judged
 *       record_id: "990000827290205171",      ← MARC control number
 *       evaluator_id: "person_ner",           ← which evaluator
 *       sub_type: "TRANSLATOR",                ← role / type / class
 *       candidate: {                            ← what was judged
 *         person/text: str, start: int, end: int,
 *         model_confidence: float, role: str, confidence: float,
 *         source: str, grounded: bool, grounded_field: str,
 *         exists_in: [{field, match_type, value}, …],
 *         // (for authority candidates: matched_name, mazal_id,
 *         //  viaf_id, wikidata_qid, …)
 *       },
 *       verdict: {
 *         name_ok: "yes"|"no"|"partial",
 *         type_ok: "yes"|"no"|"partial",
 *         role_ok: "yes"|"no"|"partial"|"n/a",
 *         overall: "full"|"partial"|"fail"|"abstain",
 *         reasoning: "Full prose explanation …"
 *       },
 *       cache_key: "<sha256>",                ← determinism signal
 *       judged_at: ISO8601,
 *       error: null
 *     }
 *
 * Search is server-side when `runId` is provided (debounced 300ms).
 * Filter-pill counts come from the server's `counts` field on the
 * first (unfiltered) load, then stay consistent with the server page.
 * Export triggers a server streaming download (no heap serialisation).
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { AiVerify, type AgentEvent } from "@/api/aiVerify";
import { ColumnFilterPopup } from "@/components/extraction/ColumnFilterPopup";
import { useDebounce } from "@/hooks/useDebounce";
import {downloadFromUrl} from "@/utils/download";
import {verdictStorageKey} from "@/utils/verdictKey";


export interface VerdictsTableProps {
  /** Map keyed by entity-id → the verdict event (live SSE path). */
  verdicts: Record<string, AgentEvent>;
  /** Called when the user clicks the record_id (opens MARC popup). */
  onOpenMarc?: (controlNumber: string) => void;
  /**
   * When provided, search and export are server-side (debounced).
   * When omitted, falls back to client-side filter over `verdicts`.
   */
  runId?: string;
  /**
   * Called when the user clicks the Fix button on a verdict row.
   * `localId` is the item/candidate local id, `target` is the field
   * to patch (e.g. "label.en"), `value` is the proposed replacement.
   */
  onApplyFix?: (localId: string, target: string, value: string) => Promise<void>;
}


type Overall = "pass" | "full" | "partial" | "fail" | "abstain" | "unknown";

type SortKey = "record" | "evaluator" | "entity" | "verdict" | "confidence";

interface ColumnFilterPopupState {
  column: SortKey;
  x: number;
  y: number;
}


export function VerdictsTable(props: VerdictsTableProps) {
  const { verdicts, onOpenMarc, runId, onApplyFix } = props;

  const rows = useMemo(() => Object.values(verdicts), [verdicts]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [overallFilter, setOverallFilter] = useState<Overall | "all">("all");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounce(search, 300);

  // Sorting
  const [sortBy, setSortBy] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  // Per-column filters (right-click popup)
  const [columnFilters, setColumnFilters] = useState<Partial<Record<SortKey, Set<string>>>>({});
  const [popup, setPopup] = useState<ColumnFilterPopupState | null>(null);

  // Fix button loading state
  const [fixingId, setFixingId] = useState<string | null>(null);

  function handleSortClick(key: SortKey) {
    setSortBy((prev) => {
      if (prev === key) {
        setSortDir((d) => d === "asc" ? "desc" : "asc");
        return key;
      }
      setSortDir("asc");
      return key;
    });
  }

  function handleHeaderRightClick(e: React.MouseEvent, col: SortKey) {
    e.preventDefault();
    setPopup({ column: col, x: e.clientX, y: e.clientY });
  }

  function handlePopupApply(col: SortKey, selected: Set<string>) {
    setColumnFilters((prev) => {
      if (selected.size === 0) {
        const next = { ...prev };
        delete next[col];
        return next;
      }
      return { ...prev, [col]: selected };
    });
    setPopup(null);
  }

  async function handleApplyFix(ev: AgentEvent) {
    const cand = (ev.candidate ?? {}) as Record<string, unknown>;
    const sf = (cand.suggested_fix ?? null) as Record<string, unknown> | null;
    if (!sf || !onApplyFix) return;
    const localId = String(
      cand._item_id ?? cand._local_id ?? cand.local_id ?? ev.record_id ?? ""
    );
    if (!localId) return;
    setFixingId(localId);
    try {
      await onApplyFix(
        localId,
        String(sf.target ?? ""),
        String(sf.value ?? ""),
      );
    } finally {
      setFixingId(null);
    }
  }

  // Server-side verdict page (only used when `runId` is available).
  const [serverRows, setServerRows] = useState<AgentEvent[] | null>(null);
  const [serverTotal, setServerTotal] = useState(0);
  const [serverCounts, setServerCounts] = useState<Record<string, number> | null>(null);
  const [serverLoading, setServerLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const useServer = runId !== undefined;

  // Fetch from server whenever search/filter changes (server path).
  useEffect(() => {
    if (!useServer) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setServerLoading(true);
    const params: Parameters<typeof AiVerify.results>[1] = { limit: 200, offset: 0 };
    if (debouncedSearch.trim()) params.q = debouncedSearch.trim();
    if (overallFilter !== "all") params.overall = overallFilter as "pass" | "partial" | "fail" | "abstain";
    AiVerify.results(runId, params)
      .then((page) => {
        if (ctrl.signal.aborted) return;
        setServerRows(page.verdicts);
        setServerTotal(page.total);
        setServerCounts(page.counts);
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        console.warn("VerdictsTable: server fetch failed, falling back to client filter", err);
        setServerRows(null);
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setServerLoading(false);
      });
    return () => ctrl.abort();
  }, [useServer, runId, debouncedSearch, overallFilter]);

  // Compute counts for the filter pills.
  // When server: use server counts (from unfiltered server response if available,
  // or from the current page counts as a best-effort).
  // When client: scan in-memory rows.
  const counts = useMemo(() => {
    if (useServer && serverCounts) return serverCounts;
    const c: Record<string, number> = {
      pass: 0, partial: 0, fail: 0, abstain: 0, unknown: 0,
    };
    for (const ev of rows) {
      const o = overall(ev);
      c[o === "full" ? "pass" : o] = (c[o === "full" ? "pass" : o] ?? 0) + 1;
    }
    return c;
  }, [rows, useServer, serverCounts]);

  // Apply client-side filter only when NOT using server path.
  const clientVisible = useMemo(() => {
    if (useServer) return [];
    const q = search.trim().toLowerCase();
    return rows.filter((ev) => {
      if (overallFilter !== "all") {
        const o = overall(ev);
        const match = overallFilter === "pass" ? o === "pass" || o === "full" : o === overallFilter;
        if (!match) return false;
      }
      if (q) {
        const hay = [
          candidateName(ev), recordId(ev),
          evaluatorId(ev), subType(ev),
          reasoning(ev),
        ].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [rows, overallFilter, search, useServer]);

  // Apply column filters and sorting to whichever row set is active.
  const visible = useMemo(() => {
    let base = useServer ? (serverRows ?? rows) : clientVisible;

    // Per-column filters (AND logic)
    for (const [colKey, allowed] of Object.entries(columnFilters) as [SortKey, Set<string>][]) {
      if (!allowed || allowed.size === 0) continue;
      base = base.filter((ev) => allowed.has(colValueForFilter(ev, colKey)));
    }

    // Sorting
    if (sortBy) {
      const dir = sortDir === "asc" ? 1 : -1;
      base = [...base].sort((a, b) => {
        const av = sortValue(a, sortBy);
        const bv = sortValue(b, sortBy);
        if (av < bv) return -1 * dir;
        if (av > bv) return 1 * dir;
        return 0;
      });
    }
    return base;
  }, [useServer, serverRows, rows, clientVisible, columnFilters, sortBy, sortDir]);

  // Distinct values for the column-filter popup.
  const popupDistinctValues = useMemo(() => {
    if (!popup) return [];
    const base = useServer ? (serverRows ?? rows) : clientVisible;
    const seen = new Map<string, number>();
    for (const ev of base) {
      const v = colValueForFilter(ev, popup.column);
      seen.set(v, (seen.get(v) ?? 0) + 1);
    }
    return Array.from(seen.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([v]) => v);
  }, [popup, useServer, serverRows, rows, clientVisible]);

  const hasActiveColFilters = Object.keys(columnFilters).length > 0;

  function toggleRow(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  function handleExportCsv() {
    if (useServer && runId) {
      const p: Record<string, string> = {};
      if (debouncedSearch.trim()) p.q = debouncedSearch.trim();
      if (overallFilter !== "all") p.overall = overallFilter;
      downloadFromUrl(
        AiVerify.exportUrl(runId, "csv", p as Parameters<typeof AiVerify.exportUrl>[2]),
        `run-${runId}-verdicts.csv`,
      );
    } else {
      copyAsCsv(visible);
    }
  }

  function handleExportJson() {
    if (useServer && runId) {
      const p: Record<string, string> = {};
      if (debouncedSearch.trim()) p.q = debouncedSearch.trim();
      if (overallFilter !== "all") p.overall = overallFilter;
      downloadFromUrl(
        AiVerify.exportUrl(runId, "json", p as Parameters<typeof AiVerify.exportUrl>[2]),
        `run-${runId}-verdicts.json`,
      );
    } else {
      copyAsJson(visible);
    }
  }

  const displayTotal = useServer ? serverTotal : rows.length;
  const displayVisible = useServer ? serverTotal : visible.length;
  const hasFixColumn = onApplyFix !== undefined;

  return (
    <section className="glass p-3 space-y-3">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <div className="kicker">
          Verdicts ({displayVisible}{displayTotal !== displayVisible && ` of ${displayTotal}`})
          {useServer && serverLoading && (
            <span className="muted text-[10px] ml-2">searching…</span>
          )}
        </div>
        <div className="flex items-center gap-2 text-[11px]">
          <FilterChip label="all" count={displayTotal}
            active={overallFilter === "all"} onClick={() => setOverallFilter("all")} />
          <FilterChip label="pass" count={counts.pass ?? 0} tone="biu-sky"
            active={overallFilter === "pass"} onClick={() => setOverallFilter("pass")} />
          <FilterChip label="partial" count={counts.partial ?? 0} tone="yellow-300"
            active={overallFilter === "partial"} onClick={() => setOverallFilter("partial")} />
          <FilterChip label="fail" count={counts.fail ?? 0} tone="red-300"
            active={overallFilter === "fail"} onClick={() => setOverallFilter("fail")} />
          <FilterChip label="abstain" count={counts.abstain ?? 0} tone="muted"
            active={overallFilter === "abstain"} onClick={() => setOverallFilter("abstain")} />
        </div>
      </div>

      <div className="flex gap-2 items-center text-xs">
        <input value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder="Search entity / record / reasoning…"
          className="input-glass !py-1 text-xs flex-1" />
        <button onClick={handleExportCsv}
          className="button-ghost !py-1 text-xs" title={useServer ? "Download filtered verdicts as CSV" : "Copy filtered verdicts as CSV (research format)"}>
          ⎘ CSV
        </button>
        <button onClick={handleExportJson}
          className="button-ghost !py-1 text-xs" title={useServer ? "Download filtered verdicts as JSON" : "Copy filtered verdicts as JSON (full schema)"}>
          ⎘ JSON
        </button>
      </div>

      <div className="overflow-x-auto -mx-3">
        <table className="w-full text-xs border-collapse">
          <thead className="muted text-left">
            <tr className="border-b border-white/5">
              <th className="py-2 pl-3 pr-2 w-6"></th>
              <SortableHeader label="Entity" sortKey="entity"
                currentSort={sortBy} currentDir={sortDir} onSort={handleSortClick}
                columnFilter={columnFilters["entity"]} onRightClick={handleHeaderRightClick} />
              <SortableHeader label="Evaluator" sortKey="evaluator"
                currentSort={sortBy} currentDir={sortDir} onSort={handleSortClick}
                columnFilter={columnFilters["evaluator"]} onRightClick={handleHeaderRightClick} />
              <th className="py-2 pr-3 text-left text-xs font-medium muted">Sub-type</th>
              <SortableHeader label="Record" sortKey="record"
                currentSort={sortBy} currentDir={sortDir} onSort={handleSortClick}
                columnFilter={columnFilters["record"]} onRightClick={handleHeaderRightClick} />
              <SortableHeader label="Overall" sortKey="verdict"
                currentSort={sortBy} currentDir={sortDir} onSort={handleSortClick}
                columnFilter={columnFilters["verdict"]} onRightClick={handleHeaderRightClick} />
              <th className="py-2 pr-3 text-left text-xs font-medium muted">Sub-judgements</th>
              <SortableHeader label="Conf." sortKey="confidence"
                currentSort={sortBy} currentDir={sortDir} onSort={handleSortClick}
                columnFilter={columnFilters["confidence"]} onRightClick={handleHeaderRightClick} />
              <th className="py-2 pr-3 hidden md:table-cell text-left text-xs font-medium muted">Judge</th>
              <th className="py-2 pr-3 hidden md:table-cell text-left text-xs font-medium muted">Judged at</th>
              {hasFixColumn && <th className="py-2 pr-3 text-left text-xs font-medium muted">Fix</th>}
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 && (
              <tr>
                <td colSpan={hasFixColumn ? 11 : 10} className="py-6 text-center muted italic">
                  {rows.length === 0 && !serverLoading ? "Waiting for verdicts…" : serverLoading ? "Searching…" : "No verdicts match the filter."}
                </td>
              </tr>
            )}
            {visible.map((ev) => {
              const key = verdictStorageKey(ev);
              const open = expanded.has(key);
              const o = overall(ev);
              const cand = (ev.candidate ?? {}) as Record<string, unknown>;
              const sf = (cand.suggested_fix ?? null) as Record<string, unknown> | null;
              const localId = String(
                cand._item_id ?? cand._local_id ?? cand.local_id ?? ev.record_id ?? ""
              );
              const isFixing = fixingId === localId;
              const showFix = hasFixColumn
                && sf != null
                && String(sf.confidence ?? "") === "high"
                && localId !== "";
              return (
                <Row key={key}
                  ev={ev} open={open} overall={o}
                  onToggle={() => toggleRow(key)}
                  onOpenMarc={onOpenMarc}
                  hasFixColumn={hasFixColumn}
                  showFix={showFix}
                  isFixing={isFixing}
                  onFix={() => { void handleApplyFix(ev); }}
                  fixReasoning={sf ? String(sf.reasoning ?? "") : ""} />
              );
            })}
          </tbody>
        </table>
      </div>

      {hasActiveColFilters && (
        <div className="flex items-center gap-2 text-[10px] muted flex-wrap">
          <span>Column filters active:</span>
          {(Object.entries(columnFilters) as [SortKey, Set<string>][]).map(([col, vals]) => (
            <span key={col}
              className="px-2 py-0.5 rounded-full bg-white/10 border border-white/20 flex items-center gap-1">
              {col}: {[...vals].join(", ")}
              <button
                onClick={() => setColumnFilters((p) => { const n = { ...p }; delete n[col]; return n; })}
                className="ml-1 hover:text-ink"
                title={`Clear filter on ${col}`}
              >×</button>
            </span>
          ))}
          <button
            onClick={() => setColumnFilters({})}
            className="px-2 py-0.5 rounded-full hover:bg-white/5 transition"
            title="Clear all column filters"
          >🗑 Clear all</button>
        </div>
      )}

      {popup && (
        <ColumnFilterPopup
          columnLabel={popup.column}
          values={popupDistinctValues}
          selected={columnFilters[popup.column] ?? new Set<string>()}
          x={popup.x}
          y={popup.y}
          onApply={(sel) => handlePopupApply(popup.column, sel)}
          onCancel={() => setPopup(null)}
        />
      )}
    </section>
  );
}


// ── Components ────────────────────────────────────────────────────────


function Row({
  ev, open, overall: o, onToggle, onOpenMarc,
  hasFixColumn, showFix, isFixing, onFix, fixReasoning,
}: {
  ev: AgentEvent;
  open: boolean;
  overall: Overall;
  onToggle: () => void;
  onOpenMarc?: (controlNumber: string) => void;
  hasFixColumn: boolean;
  showFix: boolean;
  isFixing: boolean;
  onFix: () => void;
  fixReasoning: string;
}) {
  const cand = (ev.candidate ?? {}) as Record<string, unknown>;
  const v = (ev.verdict ?? {}) as Record<string, unknown>;
  const expandedColspan = hasFixColumn ? 10 : 9;
  return (
    <>
      <tr className={`border-b border-white/5 hover:bg-white/[0.03] transition cursor-pointer`}
          onClick={onToggle}>
        <td className="py-2 pl-3 pr-2">
          <span className="muted text-[10px]">{open ? "▾" : "▸"}</span>
        </td>
        <td className="py-2 pr-3">
          <span className="text-ink">{candidateName(ev)}</span>
        </td>
        <td className="py-2 pr-3 muted">{evaluatorId(ev)}</td>
        <td className="py-2 pr-3 muted">{subType(ev)}</td>
        <td className="py-2 pr-3">
          {onOpenMarc ? (
            <span className="inline-flex items-center gap-1.5">
              <span className="font-mono">{recordId(ev)}</span>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onOpenMarc(recordId(ev)); }}
                aria-label="View full MARC record"
                title="View all MARC fields for this record (searchable)"
                className="text-biu-sky hover:text-ink text-xs leading-none px-1 py-0.5 rounded hover:bg-white/10"
              >
                📋 MARC
              </button>
            </span>
          ) : (
            <span className="font-mono">{recordId(ev)}</span>
          )}
        </td>
        <td className="py-2 pr-3"><VerdictPill overall={o} /></td>
        <td className="py-2 pr-3">
          <div className="flex gap-1">
            <SubPill label="name" value={String(v.name_ok ?? "")} />
            <SubPill label="type" value={String(v.type_ok ?? "")} />
            <SubPill label="role" value={String(v.role_ok ?? "")} />
          </div>
        </td>
        <td className="py-2 pr-3 font-mono">
          {fmtConf(ev.confidence)} <span className="muted">/ {fmtConf(cand.model_confidence)}</span>
        </td>
        <td className="py-2 pr-3 hidden md:table-cell muted font-mono text-[10px]">
          {String(ev.judge_id ?? "")}
        </td>
        <td className="py-2 pr-3 hidden md:table-cell muted font-mono text-[10px]"
            title={String(ev.judged_at ?? "")}>
          {fmtJudgedAt(ev.judged_at)}
        </td>
        {hasFixColumn && (
          <td className="py-2 pr-3" onClick={(e) => e.stopPropagation()}>
            {showFix && (
              <button
                title={fixReasoning || "Apply AI-suggested fix"}
                onClick={onFix}
                disabled={isFixing}
                className="text-xs px-2 py-1 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20 transition-colors disabled:opacity-50"
              >
                {isFixing ? "…" : "✨ Fix"}
              </button>
            )}
          </td>
        )}
      </tr>
      {open && (
        <tr className="bg-white/[0.02] border-b border-white/5">
          <td></td>
          <td colSpan={expandedColspan} className="py-3 pr-3 space-y-3">
            <ReasoningBlock ev={ev} />
            <ExistsInBlock ev={ev} />
            <AuthorityIdsBlock ev={ev} />
            <DeterminismBlock ev={ev} />
            <CandidateRawBlock ev={ev} />
          </td>
        </tr>
      )}
    </>
  );
}


function ReasoningBlock({ ev }: { ev: AgentEvent }) {
  const r = reasoning(ev);
  if (!r) return null;
  return (
    <div>
      <div className="kicker mb-1">Reasoning</div>
      <p className="text-[12px] leading-relaxed whitespace-pre-wrap text-ink/90">
        {r}
      </p>
    </div>
  );
}


function ExistsInBlock({ ev }: { ev: AgentEvent }) {
  const cand = (ev.candidate ?? {}) as Record<string, unknown>;
  const existsIn = (cand.exists_in as Array<{
    field: string; match_type: string; value: string;
  }> | undefined) ?? [];
  if (existsIn.length === 0) return null;
  return (
    <div>
      <div className="kicker mb-1">
        Provenance — MARC fields the agent grounded against ({existsIn.length})
      </div>
      <ul className="space-y-1 text-[11px]">
        {existsIn.map((row, i) => (
          <li key={i} className="flex gap-2 items-baseline">
            <span className="font-mono text-biu-sky shrink-0 w-40 truncate"
                  title={row.field}>{row.field}</span>
            <span className="muted shrink-0">{row.match_type}</span>
            <span className="text-ink/85 flex-1" dir="auto">{row.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}


function AuthorityIdsBlock({ ev }: { ev: AgentEvent }) {
  const c = (ev.candidate ?? {}) as Record<string, unknown>;
  const mazal    = String(c.mazal_id ?? "");
  const viaf     = String(c.viaf_id ?? "");
  const wikidata = String(c.wikidata_qid ?? "");
  if (!mazal && !viaf && !wikidata) return null;
  return (
    <div>
      <div className="kicker mb-1">Authority identifiers (verification sources)</div>
      <ul className="text-[11px] space-y-1">
        {mazal && <li><span className="muted">Mazal/NLI:</span>{" "}
          <a className="text-biu-sky font-mono hover:underline" target="_blank" rel="noreferrer"
             href={`https://www.nli.org.il/he/authorities/NNL_PERSON_AUTH${mazal}`}>{mazal}</a></li>}
        {viaf && <li><span className="muted">VIAF:</span>{" "}
          <a className="text-biu-sky font-mono hover:underline" target="_blank" rel="noreferrer"
             href={`https://viaf.org/viaf/${viaf}/`}>{viaf}</a></li>}
        {wikidata && <li><span className="muted">Wikidata:</span>{" "}
          <a className="text-biu-sky font-mono hover:underline" target="_blank" rel="noreferrer"
             href={`https://www.wikidata.org/wiki/${wikidata}`}>{wikidata}</a></li>}
      </ul>
    </div>
  );
}


function DeterminismBlock({ ev }: { ev: AgentEvent }) {
  const ck = String(ev.cache_key ?? "");
  const judge = String(ev.judge_id ?? "");
  const at = String(ev.judged_at ?? "");
  if (!ck && !judge && !at) return null;
  return (
    <div>
      <div className="kicker mb-1">Determinism</div>
      <ul className="text-[10px] font-mono space-y-0.5 muted">
        {judge && <li>judge: <span className="text-ink/90">{judge}</span></li>}
        {at && <li>judged at: <span className="text-ink/90">{at}</span></li>}
        {ck && (
          <li>cache key: <span className="text-ink/90" title="SHA-256 of (evaluator, sub_type, candidate, rubric, model). Identical key ⇒ identical inputs ⇒ same verdict reproducible.">
            {ck.slice(0, 16)}…{ck.slice(-8)}
          </span></li>
        )}
      </ul>
    </div>
  );
}


function CandidateRawBlock({ ev }: { ev: AgentEvent }) {
  return (
    <details className="text-[10px]">
      <summary className="muted cursor-pointer hover:text-ink">
        Raw verdict JSON
      </summary>
      <pre className="mt-1 p-2 bg-black/40 rounded-md overflow-x-auto text-[10px] font-mono leading-snug">
        {JSON.stringify(ev, null, 2)}
      </pre>
    </details>
  );
}


function SortableHeader({
  label, sortKey, currentSort, currentDir, onSort, columnFilter, onRightClick,
}: {
  label: string;
  sortKey: SortKey;
  currentSort: SortKey | null;
  currentDir: "asc" | "desc";
  onSort: (k: SortKey) => void;
  columnFilter?: Set<string>;
  onRightClick: (e: React.MouseEvent, k: SortKey) => void;
}) {
  const active = currentSort === sortKey;
  const hasFilter = columnFilter && columnFilter.size > 0;
  return (
    <th
      className="py-2 pr-3 text-left text-xs font-medium muted cursor-pointer select-none hover:text-ink transition-colors"
      onClick={() => onSort(sortKey)}
      onContextMenu={(e) => onRightClick(e, sortKey)}
      title={`Sort by ${label} (left-click) · Filter (right-click)`}
    >
      {label}
      {hasFilter && <span className="ml-1 text-biu-sky text-[9px]">▾</span>}
      <span className="ml-1 opacity-50">
        {active ? (currentDir === "asc" ? "↑" : "↓") : "↕"}
      </span>
    </th>
  );
}


function FilterChip({
  label, count, active, onClick, tone,
}: {
  label: string; count: number;
  active: boolean; onClick: () => void;
  tone?: string;
}) {
  const colour = tone ? `text-${tone}` : "text-ink";
  return (
    <button onClick={onClick}
      className={`px-2 py-0.5 rounded-full transition border ${
        active ? "bg-white/10 border-white/20" : "border-transparent hover:bg-white/5"
      } ${colour}`}>
      {label} <span className="muted">({count})</span>
    </button>
  );
}


function VerdictPill({ overall: o }: { overall: Overall }) {
  const tone = overallTone(o);
  const glyph =
    o === "pass" || o === "full" ? "✓"
    : o === "partial" ? "~"
    : o === "fail" ? "✗"
    : o === "abstain" ? "?"
    : "—";
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold ${tone}`}
          title={o}>
      {glyph} {o}
    </span>
  );
}


function SubPill({ label, value }: { label: string; value: string }) {
  const v = value.toLowerCase();
  const tone =
    v === "yes" ? "text-biu-sky"
    : v === "partial" ? "text-yellow-300"
    : v === "no"  ? "text-red-300"
    : "muted";
  const glyph = v === "yes" ? "✓" : v === "partial" ? "~" : v === "no" ? "✗" : "—";
  return (
    <span className={`text-[9px] font-mono ${tone}`} title={`${label}: ${value}`}>
      {label[0]}{glyph}
    </span>
  );
}


// ── Helpers ────────────────────────────────────────────────────────────


function overall(ev: AgentEvent): Overall {
  const v = (ev.verdict ?? {}) as Record<string, unknown>;
  const o = String(v.overall ?? "").toLowerCase();
  if (o === "full" || o === "pass") return "pass";
  if (o === "partial" || o === "fail" || o === "abstain") return o as Overall;
  return "unknown";
}


function overallTone(o: Overall): string {
  return o === "pass" || o === "full" ? "text-biu-sky"
       : o === "partial" ? "text-yellow-300"
       : o === "fail"    ? "text-red-300"
       : o === "abstain" ? "muted"
       : "muted";
}


function candidateName(ev: AgentEvent): string {
  const c = (ev.candidate ?? {}) as Record<string, unknown>;
  return String(c.person ?? c.text ?? c.entity_text ?? c.name ?? c.label ?? "(unknown)");
}


function recordId(ev: AgentEvent): string {
  return String(ev.record_id ?? "");
}


function evaluatorId(ev: AgentEvent): string {
  return String(ev.evaluator_id ?? "");
}


function subType(ev: AgentEvent): string {
  return String(ev.sub_type ?? "");
}


function reasoning(ev: AgentEvent): string {
  const v = (ev.verdict ?? {}) as Record<string, unknown>;
  return String(v.reasoning ?? "");
}


function sortValue(ev: AgentEvent, key: SortKey): string {
  switch (key) {
    case "entity": return candidateName(ev).toLowerCase();
    case "evaluator": return evaluatorId(ev).toLowerCase();
    case "record": return recordId(ev).toLowerCase();
    case "verdict": return overall(ev);
    case "confidence": {
      const n = typeof ev.confidence === "number" ? ev.confidence : parseFloat(String(ev.confidence ?? ""));
      // Sort descending by numeric value when using "asc" label (high conf first)
      return isFinite(n) ? String(1 - n) : "1";
    }
  }
}


function colValueForFilter(ev: AgentEvent, key: SortKey): string {
  switch (key) {
    case "entity": return candidateName(ev);
    case "evaluator": return evaluatorId(ev);
    case "record": return recordId(ev);
    case "verdict": return overall(ev);
    case "confidence": return fmtConf(ev.confidence);
  }
}


function fmtConf(c: unknown): string {
  if (c == null || c === "") return "—";
  const n = typeof c === "number" ? c : parseFloat(String(c));
  return isFinite(n) ? n.toFixed(2) : "—";
}


function fmtJudgedAt(at: unknown): string {
  if (!at) return "—";
  try {
    const d = new Date(String(at));
    return d.toLocaleString();
  } catch {
    return String(at);
  }
}


// Client-side fallback serialisers (used when runId is not provided).

function copyAsCsv(rows: AgentEvent[]) {
  const headers = [
    "record_id", "evaluator_id", "sub_type", "candidate",
    "overall", "name_ok", "type_ok", "role_ok",
    "judge_id", "judged_at", "cache_key", "reasoning",
  ];
  const lines = [headers.join(",")];
  for (const ev of rows) {
    const v = (ev.verdict ?? {}) as Record<string, unknown>;
    const row = [
      recordId(ev), evaluatorId(ev), subType(ev), candidateName(ev),
      overall(ev), String(v.name_ok ?? ""), String(v.type_ok ?? ""), String(v.role_ok ?? ""),
      String(ev.judge_id ?? ""), String(ev.judged_at ?? ""), String(ev.cache_key ?? ""),
      reasoning(ev),
    ].map(_csvCell).join(",");
    lines.push(row);
  }
  void navigator.clipboard.writeText(lines.join("\n"));
}


function _csvCell(s: string): string {
  if (s.includes('"') || s.includes(",") || s.includes("\n")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}


function copyAsJson(rows: AgentEvent[]) {
  void navigator.clipboard.writeText(JSON.stringify(rows, null, 2));
}
