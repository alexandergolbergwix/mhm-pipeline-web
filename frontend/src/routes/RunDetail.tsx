import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import { useProjectEvents } from "@/api/realtime";
import {
  Runs, streamAuthorityEnrich, type AuthorityMatch, type RunDetail as Detail,
} from "@/api/runs";
import {
  applyConditions, type FilterCondition, StructuredFilter,
} from "@/components/StructuredFilter";
import {
  ConfidenceBadge, MatchDetailDialog, VerdictBadge,
} from "@/components/MatchDetailDialog";
import { MarcRecordPopup } from "@/components/MarcRecordPopup";
import { SelectAllVisible } from "@/components/SelectAllVisible";
import { AiVerificationModal } from "@/components/AiVerificationModal";
import { HistoryTimeline } from "@/components/history/HistoryTimeline";
import type { ScopeKind } from "@/api/aiVerify";
import { SectionExportMenu } from "@/components/export/SectionExportMenu";
import { SectionImportButton } from "@/components/import/SectionImportButton";

type EnrichPhase = "idle" | "running" | "done" | "error";

const COLUMNS = [
  { key: "control_number", label: "Record",     kind: "text" as const, sortable: true  },
  { key: "entity_text",    label: "Entity",     kind: "text" as const, sortable: true  },
  { key: "role",           label: "Role",       kind: "text" as const, sortable: true  },
  { key: "matched_name",   label: "Matched",    kind: "text" as const, sortable: true  },
  { key: "confidence",     label: "Confidence", kind: "text" as const, sortable: true  },
  { key: "source",         label: "Sources",    kind: "text" as const, sortable: true  },
  { key: "ai_verdict",     label: "AI verdict", kind: "text" as const, sortable: true  },
  { key: "approved",       label: "Approved",   kind: "boolean" as const, sortable: true },
];

type SortDir = "asc" | "desc";
const CONFIDENCE_ORDER: Record<string, number> = { low: 0, medium: 1, high: 2 };
const VERDICT_ORDER:    Record<string, number> = { fail: 0, partial: 1, abstain: 2, pass: 3 };

export default function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conditions, setConditions] = useState<FilterCondition[]>([]);
  const [marcPopupCn, setMarcPopupCn] = useState<string | null>(null);
  const [openMatch, setOpenMatch] = useState<AuthorityMatch | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [backfillBusy, setBackfillBusy] = useState(false);
  const [backfillResult, setBackfillResult] = useState<{
    checked: number; updated: number; births_filled: number; deaths_filled: number;
  } | null>(null);
  const [reEnrichSkipCache, setReEnrichSkipCache] = useState(false);
  // SSE-based re-enrichment progress
  const [enrichPhase, setEnrichPhase] = useState<EnrichPhase>("idle");
  const [enrichTotal, setEnrichTotal] = useState(0);
  const [enrichProcessed, setEnrichProcessed] = useState(0);
  const [enrichCurrentEntity, setEnrichCurrentEntity] = useState<string>("");
  const [enrichCurrentSource, setEnrichCurrentSource] = useState<string | null>(null);
  const [enrichCurrentMatched, setEnrichCurrentMatched] = useState<boolean | null>(null);
  const [enrichResult, setEnrichResult] = useState<{
    checked: number; updated: number; newly_matched: number; skip_cache: boolean;
  } | null>(null);
  const [enrichError, setEnrichError] = useState<string | null>(null);
  const enrichCancelRef = useRef<(() => void) | null>(null);
  // Live elapsed-time ticker
  const [enrichStartedAt, setEnrichStartedAt] = useState<number | null>(null);
  const [enrichTick, setEnrichTick] = useState(0);
  useEffect(() => {
    if (enrichStartedAt === null) return;
    const id = window.setInterval(() => setEnrichTick((t) => t + 1), 500);
    return () => window.clearInterval(id);
  }, [enrichStartedAt]);
  void enrichTick;
  const [verifyScope, setVerifyScope] = useState<{ kind: ScopeKind; matchIds?: string[]; label: string } | null>(null);
  const [historyFor, setHistoryFor] = useState<{ id: string } | null>(null);
  // Sort state — persisted across reloads so curators don't lose their place.
  const [sortKey, setSortKey] = useState<string | null>(() =>
    localStorage.getItem("mhm.runDetail.sortKey") || null);
  const [sortDir, setSortDir] = useState<SortDir>(() =>
    (localStorage.getItem("mhm.runDetail.sortDir") as SortDir) || "asc");
  useEffect(() => {
    if (sortKey) localStorage.setItem("mhm.runDetail.sortKey", sortKey);
    else         localStorage.removeItem("mhm.runDetail.sortKey");
    localStorage.setItem("mhm.runDetail.sortDir", sortDir);
  }, [sortKey, sortDir]);

  async function refresh() {
    if (!runId) return;
    try {
      setRun(await Runs.get(runId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }
  useEffect(() => { void refresh(); }, [runId]);

  useProjectEvents(run?.project_id, (msg) => {
    if (msg.type.startsWith("match.") || msg.type === "snapshot.restored") {
      void refresh();
    }
  });

  async function toggle(m: AuthorityMatch) {
    if (!runId) return;
    try {
      const updated = await Runs.setApproval(runId, m.id, !m.approved);
      patchMatch(updated);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function bulk(approved: boolean) {
    if (!runId || selected.size === 0) return;
    try {
      const updated = await Runs.bulkApprove(runId, Array.from(selected), approved);
      setRun((prev) => {
        if (!prev) return prev;
        const m = new Map(prev.matches.map((x) => [x.id, x] as const));
        for (const u of updated) m.set(u.id, u);
        return { ...prev, matches: Array.from(m.values()) };
      });
      setSelected(new Set());
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function backfillDates() {
    if (!runId) return;
    setBackfillBusy(true); setError(null); setBackfillResult(null);
    try {
      const r = await Runs.backfillDates(runId);
      setBackfillResult(r);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBackfillBusy(false);
    }
  }

  async function reEnrich() {
    if (!runId) return;
    setEnrichPhase("running");
    setEnrichError(null);
    setEnrichResult(null);
    setBackfillResult(null);
    setEnrichProcessed(0);
    setEnrichTotal(0);
    setEnrichCurrentEntity("");
    setEnrichCurrentSource(null);
    setEnrichCurrentMatched(null);
    setEnrichStartedAt(Date.now());

    const {events, cancel} = streamAuthorityEnrich(runId, reEnrichSkipCache);
    enrichCancelRef.current = cancel;

    try {
      for await (const ev of events) {
        if (ev.type === "authority.start") {
          setEnrichTotal(Number(ev.total_entities ?? 0));
        } else if (ev.type === "authority.entity") {
          setEnrichProcessed(Number(ev.index ?? 0) + 1);
          setEnrichCurrentEntity(String(ev.entity_text ?? ""));
          setEnrichCurrentSource(ev.source != null ? String(ev.source) : null);
          setEnrichCurrentMatched(Boolean(ev.matched));
        } else if (ev.type === "authority.done") {
          setEnrichResult({
            checked: Number(ev.checked ?? 0),
            updated: Number(ev.updated ?? 0),
            newly_matched: Number(ev.newly_matched ?? 0),
            skip_cache: Boolean(ev.skip_cache),
          });
          setEnrichPhase("done");
          await refresh();
        } else if (ev.type === "authority.error") {
          setEnrichError(String(ev.message ?? "Unknown error"));
          setEnrichPhase("error");
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setEnrichError(e instanceof ApiError ? e.detail : String(e));
        setEnrichPhase("error");
      }
    } finally {
      enrichCancelRef.current = null;
      setEnrichStartedAt(null);
    }
  }

  function cancelEnrich() {
    enrichCancelRef.current?.();
    setEnrichPhase("idle");
    setEnrichStartedAt(null);
  }

  function patchMatch(next: AuthorityMatch) {
    setRun((prev) =>
      prev
        ? { ...prev, matches: prev.matches.map((x) => (x.id === next.id ? next : x)) }
        : prev,
    );
    if (openMatch && openMatch.id === next.id) setOpenMatch(next);
  }

  function openRecord(cn: string) {
    setMarcPopupCn(cn);
  }

  const distinct = useMemo<Record<string, string[]>>(() => {
    if (!run) return {};
    const acc: Record<string, Set<string>> = {};
    for (const m of run.matches) {
      for (const c of COLUMNS) {
        acc[c.key] = acc[c.key] ?? new Set();
        acc[c.key].add(cellString(m, c.key));
      }
    }
    const out: Record<string, string[]> = {};
    for (const k of Object.keys(acc)) out[k] = Array.from(acc[k]).filter(Boolean).sort();
    return out;
  }, [run]);

  const filtered = useMemo(() => {
    if (!run) return [];
    const rows = applyConditions(run.matches, conditions, (m, col) => cellString(m, col));
    if (!sortKey) return rows;
    const cmp = sortComparator(sortKey, sortDir);
    return [...rows].sort(cmp);
  }, [run, conditions, sortKey, sortDir]);

  // Cycle: not-sorted → asc → desc → not-sorted.
  function toggleSort(col: string) {
    if (sortKey !== col)     { setSortKey(col); setSortDir("asc"); return; }
    if (sortDir === "asc")   { setSortDir("desc");                  return; }
    setSortKey(null); setSortDir("asc");
  }

  function selectAllVisible() {
    const next = new Set(selected);
    filtered.forEach((m) => next.add(m.id));
    setSelected(next);
  }
  function clearSelection() { setSelected(new Set()); }
  // Count how many of the currently-selected rows are ALSO visible —
  // used for the indeterminate state on the SelectAllVisible widget.
  const selectedInVisible = filtered.reduce((n, m) => n + (selected.has(m.id) ? 1 : 0), 0);

  if (error)
    return <Layout><div className="glass p-6 text-red-300">{error}</div></Layout>;
  if (!run)
    return <Layout><p className="muted">Loading run…</p></Layout>;

  return (
    <Layout>
      <div className="space-y-6">
        <section className="glass p-6 space-y-2">
          <div className="kicker">
            Run ·{" "}
            <Link to={`/projects/${run.project_id}`} className="hover:text-ink underline">
              back to project
            </Link>
            {" · "}
            <Link to={`/runs/${run.id}/overview`} className="hover:text-ink underline">
              Overview
            </Link>
            {" · "}
            <Link to={`/runs/${run.id}/extraction`} className="hover:text-ink underline">
              Extraction
            </Link>
            {" · "}
            <Link to={`/runs/${run.id}/rdf`} className="hover:text-ink underline">
              RDF
            </Link>
            {" · "}
            <Link to={`/runs/${run.id}/hmo-studio`} className="hover:text-ink underline">
              HMO Studio
            </Link>
            {" · "}
            <Link to={`/runs/${run.id}/wikidata-studio`} className="hover:text-ink underline">
              Wikidata Studio
            </Link>
          </div>
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-2xl font-semibold">{run.name}</h2>
            <div className="flex items-center gap-3">
              <Link to={`/runs/${run.id}/wikidata-studio`} className="button-ghost text-sm">
                Wikidata Studio →
              </Link>
              <StatusPill status={run.status} />
            </div>
          </div>
          <p className="muted text-sm">
            {run.record_count} record{run.record_count === 1 ? "" : "s"} ·{" "}
            {run.match_count} candidate match{run.match_count === 1 ? "" : "es"} ·{" "}
            {new Date(run.created_at).toLocaleString()}
          </p>
          {run.error && <p className="text-red-300 text-sm">{run.error}</p>}
        </section>

        <section className="glass p-6 space-y-4">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <div className="kicker">Authority candidates</div>
              <h3 className="text-lg font-medium">
                Review &amp; approve · <span className="muted">{filtered.length} of {run.matches.length}</span>
              </h3>
            </div>
            <div className="flex gap-2 items-center flex-wrap">
              <button disabled={selected.size === 0} onClick={() => bulk(true)}  className="button-primary !py-1.5 text-sm">Approve selected</button>
              <button disabled={selected.size === 0} onClick={() => bulk(false)} className="button-ghost   !py-1.5 text-sm">Unapprove selected</button>
              <button disabled={selected.size === 0}
                      onClick={() => setVerifyScope({
                        kind: "selection",
                        matchIds: Array.from(selected),
                        label: `${selected.size} selected`,
                      })}
                      className="button-ghost !py-1.5 text-sm text-biu-sky">
                ✨ Verify selected with AI
              </button>
              <button onClick={() => setVerifyScope({
                        kind: "all",
                        matchIds: filtered.map((m) => m.id),
                        label: `${filtered.length} visible`,
                      })}
                      disabled={filtered.length === 0}
                      className="button-ghost !py-1.5 text-sm text-biu-sky">
                ✨ Verify all visible with AI
              </button>
              <button onClick={backfillDates} disabled={!!backfillBusy}
                      title="Pull birth / death years from the IDs already stored (Mazal · VIAF · Wikidata) without re-running enrichment. Fixes matches that show '—' in the Dates tab."
                      className="button-ghost !py-1.5 text-sm text-amber-400">
                {backfillBusy ? "⏳ Backfilling…" : "📅 Backfill dates"}
              </button>
              {/* Re-run the full Mazal/VIAF/Wikidata matching while preserving approvals */}
              <div className="flex items-center gap-1.5 border border-white/10 rounded px-2 py-1">
                <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer select-none">
                  <input
                    type="checkbox"
                    className="accent-biu-sky"
                    checked={reEnrichSkipCache}
                    onChange={(e) => setReEnrichSkipCache(e.target.checked)}
                    disabled={enrichPhase === "running"}
                  />
                  Skip cache
                </label>
                {enrichPhase === "running" ? (
                  <button
                    onClick={cancelEnrich}
                    className="button-ghost !py-0.5 !px-2 text-xs text-red-400 whitespace-nowrap">
                    ✕ Cancel
                  </button>
                ) : (
                  <button
                    onClick={reEnrich}
                    title={reEnrichSkipCache
                      ? "Re-run full Mazal · VIAF · Wikidata · KIMA matching with fresh API calls (ignores 30-day cache). Preserves approvals."
                      : "Re-run full Mazal · VIAF · Wikidata · KIMA matching, using cached results where available. Preserves approvals."}
                    className="button-ghost !py-0.5 !px-2 text-xs text-biu-sky whitespace-nowrap">
                    ↻ Re-run enrichment
                  </button>
                )}
              </div>
              {runId && (
                <SectionExportMenu
                  section="authority"
                  runId={runId}
                  availableFormats={["json", "csv"]}
                />
              )}
              {runId && (
                <SectionImportButton
                  section="authority"
                  runId={runId}
                  onComplete={() => {
                    if (runId) Runs.get(runId).then((d) => setRun(d)).catch(() => null);
                  }}
                />
              )}
              {backfillResult && (
                <span className="muted text-xs">
                  {backfillResult.updated > 0
                    ? <>✓ {backfillResult.updated} rows updated · +{backfillResult.births_filled} births · +{backfillResult.deaths_filled} deaths</>
                    : <>No new dates available from Mazal / VIAF / Wikidata for the remaining matches</>}
                </span>
              )}
              {enrichResult && enrichPhase === "done" && (
                <span className="muted text-xs">
                  {enrichResult.updated > 0 || enrichResult.newly_matched > 0
                    ? <>✓ {enrichResult.updated} updated · {enrichResult.newly_matched} new
                        {enrichResult.skip_cache && <> · cache bypassed</>}</>
                    : <>No changes — all matches already up to date</>}
                </span>
              )}
            </div>
          </div>

          {/* Live re-enrichment progress panel */}
          {enrichPhase === "running" && (
            <div className="glass rounded-lg p-4 space-y-3 border border-biu-sky/20">
              <div className="flex items-center justify-between gap-3">
                <div className="kicker text-biu-sky flex items-center gap-2">
                  <span className="animate-pulse">●</span> Re-enriching authority candidates…
                </div>
                {enrichStartedAt && (
                  <span className="muted text-[11px]">
                    {((Date.now() - enrichStartedAt) / 1000).toFixed(1)}s elapsed
                  </span>
                )}
              </div>
              {/* Progress bar */}
              <div className="w-full h-1.5 bg-white/10 rounded-full overflow-hidden">
                <div
                  className="h-full bg-biu-sky rounded-full transition-all duration-300"
                  style={{width: enrichTotal > 0 ? `${Math.round((enrichProcessed / enrichTotal) * 100)}%` : "0%"}}
                />
              </div>
              <div className="flex items-center justify-between gap-3 text-xs">
                <div className="flex items-center gap-2 min-w-0">
                  {enrichCurrentEntity && (
                    <>
                      <span className="muted shrink-0">Entity:</span>
                      <span className="text-ink truncate max-w-xs">{enrichCurrentEntity}</span>
                      {enrichCurrentMatched && enrichCurrentSource && (
                        <span className="glass-pill px-2 py-[1px] text-[10px] uppercase tracking-wider text-biu-sky shrink-0">
                          {enrichCurrentSource}
                        </span>
                      )}
                      {enrichCurrentMatched === false && (
                        <span className="muted text-[10px] shrink-0">no match</span>
                      )}
                    </>
                  )}
                </div>
                <span className="muted shrink-0 tabular-nums">
                  {enrichProcessed} / {enrichTotal}
                  {enrichTotal > 0 && (
                    <> ({Math.round((enrichProcessed / enrichTotal) * 100)}%)</>
                  )}
                </span>
              </div>
            </div>
          )}

          {/* Error banner */}
          {enrichPhase === "error" && enrichError && (
            <div className="glass rounded-lg p-3 border border-red-400/30 text-red-300 text-sm">
              ✕ Re-enrichment failed: {enrichError}
            </div>
          )}

          <StructuredFilter
            columns={COLUMNS}
            distinctValues={distinct}
            conditions={conditions}
            onChange={setConditions}
          />

          <div className="flex items-center justify-between gap-3 flex-wrap pt-1">
            <SelectAllVisible
              visibleCount={filtered.length}
              selectedCount={selectedInVisible}
              onSelectAll={selectAllVisible}
              onClear={clearSelection}
              label="matches" />
            {sortKey && (
              <button onClick={() => setSortKey(null)}
                      className="text-xs text-biu-sky hover:underline">
                Clear sort
              </button>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead className="muted text-left">
                <tr className="border-b border-white/5">
                  <th className="py-2 pr-2"></th>
                  {COLUMNS.map((c) => (
                    <th key={c.key} className="py-2 pr-3 select-none">
                      {c.sortable
                        ? <button onClick={() => toggleSort(c.key)}
                                  className="inline-flex items-center gap-1 hover:text-ink transition">
                            <span>{c.label}</span>
                            <SortGlyph active={sortKey === c.key} dir={sortDir} />
                          </button>
                        : c.label}
                    </th>
                  ))}
                  <th className="py-2 pr-3"></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((m) => {
                  const p = (m.payload ?? {}) as Record<string, unknown>;
                  const ai = (p.ai_verdict ?? null) as null | { overall: string };
                  const guards = (p.guard_flags as string[] | undefined) ?? [];
                  const sourceCount = Number(p.source_count ?? 0);
                  return (
                    <tr key={m.id} className="border-b border-white/5 hover:bg-white/[0.03] transition">
                      <td className="py-2 pr-2">
                        <input type="checkbox"
                               checked={selected.has(m.id)}
                               onChange={(e) => {
                                 const next = new Set(selected);
                                 if (e.target.checked) next.add(m.id); else next.delete(m.id);
                                 setSelected(next);
                               }} />
                      </td>
                      <td className="py-2 pr-3">
                        <button onClick={() => openRecord(m.control_number)}
                                className="text-biu-sky hover:underline font-mono text-xs">
                          {m.control_number}
                        </button>
                      </td>
                      <td className="py-2 pr-3 max-w-xs truncate">{m.entity_text}</td>
                      <td className="py-2 pr-3"><span className="kicker">{m.role || "—"}</span></td>
                      <td className="py-2 pr-3 max-w-xs truncate">
                        {m.matched_name || <span className="muted">—</span>}
                        {guards.length > 0 && (
                          <span className="text-red-300 ml-1" title={guards.join(", ")}>⚠</span>
                        )}
                      </td>
                      <td className="py-2 pr-3">
                        <span title={confidenceTooltip(m)} className="cursor-help">
                          <ConfidenceBadge confidence={m.confidence} />
                        </span>
                      </td>
                      <td className="py-2 pr-3">
                        <SourcesCell match={m} sourceCount={sourceCount} />
                      </td>
                      <td className="py-2 pr-3">
                        {ai
                          ? <VerdictBadge overall={ai.overall} />
                          : <span className="muted text-xs italic">—</span>}
                      </td>
                      <td className="py-2 pr-3">
                        <input type="checkbox" checked={m.approved} onChange={() => toggle(m)} />
                      </td>
                      <td className="py-2 pr-1 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <button onClick={() => setOpenMatch(m)}
                                  className="button-ghost text-xs">Details</button>
                          <button onClick={() => setOpenMatch(m)}
                                  data-testid={`match-edit-${m.id}`}
                                  className="button-ghost text-xs"
                                  title="Open details to edit match fields">
                            Edit
                          </button>
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
                  );
                })}
                {filtered.length === 0 && (
                  <tr><td colSpan={COLUMNS.length + 2} className="py-6 text-center muted">No rows match this filter.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {marcPopupCn && runId && (
        <MarcRecordPopup runId={runId} controlNumber={marcPopupCn}
                         onClose={() => setMarcPopupCn(null)} />
      )}
      {openMatch && (
        <MatchDetailDialog
          runId={runId!}
          match={openMatch}
          onClose={() => setOpenMatch(null)}
          onPatched={patchMatch}
        />
      )}
      {verifyScope && runId && (
        <AiVerificationModal
          runId={runId}
          scopeKind={verifyScope.kind}
          matchIds={verifyScope.matchIds}
          scopeLabel={verifyScope.label}
          onClose={() => setVerifyScope(null)}
        />
      )}
      {historyFor && run ? (
        <aside
          data-testid="authority-history-drawer"
          className="fixed right-0 top-0 h-full w-[460px] glass shadow-2xl z-50 overflow-auto"
        >
          <HistoryTimeline
            projectId={run.project_id}
            entityType="authority_match"
            entityId={historyFor.id}
            onClose={() => setHistoryFor(null)}
          />
        </aside>
      ) : null}
    </Layout>
  );
}


// ── helpers ─────────────────────────────────────────────────────────────


function cellString(m: AuthorityMatch, col: string): string {
  if (col === "ai_verdict") {
    const v = m.payload?.ai_verdict as { overall?: string } | undefined;
    return v?.overall ?? "";
  }
  if (col === "source") {
    const list = m.payload?.sources as string[] | undefined;
    if (list?.length) return list.join(",");
  }
  return String((m as unknown as Record<string, unknown>)[col] ?? "");
}


/** Tiny up/down/idle indicator next to a sortable header. */
function SortGlyph({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active)        return <span className="muted text-[9px]">⇅</span>;
  if (dir === "asc")  return <span className="text-biu-sky text-[10px]">↑</span>;
  return                    <span className="text-biu-sky text-[10px]">↓</span>;
}


/** Returns a stable comparator for the given key + direction. Uses
 *  per-column orderings for confidence and ai_verdict (those aren't
 *  alphabetical) and source_count first for the Sources column. */
function sortComparator(
  key: string, dir: SortDir,
): (a: AuthorityMatch, b: AuthorityMatch) => number {
  const mul = dir === "asc" ? 1 : -1;
  return (a, b) => {
    let r = 0;
    if (key === "confidence") {
      r = (CONFIDENCE_ORDER[a.confidence] ?? -1) - (CONFIDENCE_ORDER[b.confidence] ?? -1);
    } else if (key === "ai_verdict") {
      const av = (a.payload?.ai_verdict as { overall?: string } | undefined)?.overall ?? "";
      const bv = (b.payload?.ai_verdict as { overall?: string } | undefined)?.overall ?? "";
      r = (VERDICT_ORDER[av] ?? -1) - (VERDICT_ORDER[bv] ?? -1);
    } else if (key === "source") {
      const ac = Number(a.payload?.source_count ?? 0);
      const bc = Number(b.payload?.source_count ?? 0);
      r = ac - bc;
      if (r === 0) r = cellString(a, key).localeCompare(cellString(b, key));
    } else if (key === "approved") {
      r = (a.approved ? 1 : 0) - (b.approved ? 1 : 0);
    } else {
      r = cellString(a, key).localeCompare(cellString(b, key), undefined,
                                           { sensitivity: "base", numeric: true });
    }
    return r * mul;
  };
}


function confidenceTooltip(m: AuthorityMatch): string {
  const p = (m.payload ?? {}) as Record<string, unknown>;
  const sources = (p.sources as string[] | undefined) ?? [];
  const guards  = (p.guard_flags as string[] | undefined) ?? [];
  const lines: string[] = [
    `Confidence: ${m.confidence}`,
    `Sources (${sources.length}): ${sources.join(", ") || "none"}`,
    guards.length ? `Guards fired: ${guards.join(", ")}` : "No guards fired",
  ];
  const reasoning = p.reasoning as string | undefined;
  if (reasoning) lines.push("", reasoning);
  return lines.join("\n");
}


function SourcesCell({ match, sourceCount }: { match: AuthorityMatch; sourceCount: number }) {
  const sources = (match.payload?.sources as string[] | undefined) ?? [];
  if (sources.length === 0) {
    return <span className="muted text-xs italic">—</span>;
  }
  return (
    <span className="inline-flex items-center gap-1 flex-wrap"
          title={`Sources: ${sources.join(", ")}${sourceCount >= 2 ? ` · cross-source ×${sourceCount}` : ""}`}>
      {sources.map((s) => (
        <span key={s}
              className="glass-pill px-2 py-[1px] text-[10px] uppercase tracking-wider whitespace-nowrap shrink-0">
          {s}
        </span>
      ))}
      {sourceCount >= 2 && (
        <span className="text-biu-sky text-[10px] shrink-0" title="Cross-source agreement">✓×{sourceCount}</span>
      )}
    </span>
  );
}


function StatusPill({ status }: { status: string }) {
  const tone =
    status === "succeeded" ? "text-biu-sky"
    : status === "running" ? "text-yellow-300"
    : status === "failed"  ? "text-red-300"
    : "muted";
  return <span className={`glass-pill px-3 py-1 text-[10px] kicker ${tone}`}>{status}</span>;
}


