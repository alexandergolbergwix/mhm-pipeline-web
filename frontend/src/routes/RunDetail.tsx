import {useCallback, useEffect, useState} from "react";
import { Link, useParams } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { ApiError } from "@/api/client";
import { useProjectEvents } from "@/api/realtime";
import {
  Runs, type AuthorityMatch, type RunDetail as Detail,
} from "@/api/runs";
import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import { AuthorityMatchEditDialog } from "@/components/AuthorityMatchEditDialog";
import { AiVerificationModal } from "@/components/AiVerificationModal";
import type { ScopeKind } from "@/api/aiVerify";
import { SectionExportMenu } from "@/components/export/SectionExportMenu";
import { SectionImportButton } from "@/components/import/SectionImportButton";
import { AuthorityTable } from "@/components/authority/AuthorityTable";
import { AuthorityMatchingHelp } from "@/components/authority/AuthorityMatchingHelp";
import { AuthorityDetailDrawer } from "@/components/authority/AuthorityDetailDrawer";
import { AuthorityAutoApproveRuleBuilder } from "@/components/authority/AuthorityAutoApproveRuleBuilder";
import {Glass, GlassPill} from "@/components/glass";
import {useRunJobAttachment} from "@/hooks/useRunJobAttachment";
import {
  canAuthorityAutoFix,
  getAuthoritySuggestedFix,
  resolveAuthorityFixPatch,
} from "@/utils/authorityAutofix";

type EnrichPhase = "idle" | "running" | "done" | "error";

function toNumberRecord(value: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (typeof value !== "object" || value === null) return out;
  for (const [key, raw] of Object.entries(value)) {
    const n = Number(raw);
    if (Number.isFinite(n)) out[key] = n;
  }
  return out;
}

export default function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  // openDrawerMatch: drives AuthorityDetailDrawer (replaces MatchDetailDialog)
  const [openDrawerMatch, setOpenDrawerMatch] = useState<AuthorityMatch | null>(null);
  // openEditMatch: drives AuthorityMatchEditDialog directly (from table ✎ button)
  const [openEditMatch, setOpenEditMatch] = useState<AuthorityMatch | null>(null);
  const [showAutoApprove, setShowAutoApprove] = useState(false);
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
    checked: number;
    matched: number;
    updated: number;
    newly_matched: number;
    source_counts: Record<string, number>;
    skip_cache: boolean;
  } | null>(null);
  const [enrichError, setEnrichError] = useState<string | null>(null);
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
  const [filteredMatchIds, setFilteredMatchIds] = useState<string[]>([]);
  const [fixableMatchIds, setFixableMatchIds] = useState<string[]>([]);
  const [bulkFixBusy, setBulkFixBusy] = useState(false);
  const [noteIndex, setNoteIndex] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    if (!runId) return;
    try {
      const [detail, notes] = await Promise.all([
        Runs.get(runId),
        Runs.noteIndex(runId).catch(() => ({})),
      ]);
      setRun(detail);
      setNoteIndex(notes);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, [runId]);
  useEffect(() => { void refresh(); }, [refresh]);

  const syncEnrichFromJob = useCallback((job: RunJobSnapshot) => {
    const p = job.progress ?? {};
    if (job.status === "queued" || job.status === "running") {
      setEnrichPhase("running");
      setEnrichError(null);
      setEnrichResult(null);
      setEnrichTotal(Number(p.total ?? 0));
      setEnrichProcessed(Number(p.processed ?? 0));
      setEnrichCurrentEntity(String(p.current_entity ?? p.message ?? ""));
      setEnrichCurrentSource(
        p.current_source != null ? String(p.current_source) : null,
      );
      setEnrichCurrentMatched(
        typeof p.matched === "boolean" ? p.matched : null,
      );
      setEnrichStartedAt((prev) => prev ?? Date.now());
      return;
    }
    setEnrichStartedAt(null);
    if (job.status === "succeeded" && job.result) {
      setEnrichResult({
        checked: Number(job.result.checked ?? 0),
        matched: Number(job.result.matched ?? 0),
        updated: Number(job.result.updated ?? 0),
        newly_matched: Number(job.result.newly_matched ?? 0),
        source_counts: toNumberRecord(job.result.source_counts),
        skip_cache: Boolean(job.result.skip_cache),
      });
      setEnrichPhase("done");
      void refresh();
      return;
    }
    if (job.status === "cancelled") {
      setEnrichPhase("idle");
      return;
    }
    if (job.status === "failed") {
      setEnrichError(job.error ?? "Re-enrich failed");
      setEnrichPhase("error");
    }
  }, [refresh]);

  const {
    activeJob: enrichJob,
    trackedJobId,
    setTrackedJobId,
    ensureJobPolling,
    cancelJob: cancelRunJob,
  } = useRunJobAttachment(runId, "authority_re_enrich", syncEnrichFromJob);

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
    setEnrichError(null);
    setEnrichResult(null);
    setBackfillResult(null);
    setEnrichProcessed(0);
    setEnrichTotal(0);
    setEnrichCurrentEntity("");
    setEnrichCurrentSource(null);
    setEnrichCurrentMatched(null);
    setEnrichPhase("running");
    setEnrichStartedAt(Date.now());

    try {
      const job = await RunJobs.start(runId, "authority_re_enrich", {
        skip_cache: reEnrichSkipCache,
      });
      setTrackedJobId(job.id);
      syncEnrichFromJob(job);
      ensureJobPolling();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && runId) {
        const {jobs} = await RunJobs.listForRun(runId, true);
        const active = jobs.find((j) => j.kind === "authority_re_enrich");
        if (active) {
          setTrackedJobId(active.id);
          syncEnrichFromJob(active);
          ensureJobPolling();
          return;
        }
      }
      setEnrichError(e instanceof ApiError ? e.detail : String(e));
      setEnrichPhase("error");
      setEnrichStartedAt(null);
    }
  }

  function cancelEnrich() {
    if (!runId) return;
    const id = trackedJobId ?? enrichJob?.id;
    if (id) void cancelRunJob(runId, id);
    setEnrichPhase("idle");
    setEnrichStartedAt(null);
  }

  function patchMatch(next: AuthorityMatch) {
    setRun((prev) =>
      prev
        ? { ...prev, matches: prev.matches.map((x) => (x.id === next.id ? next : x)) }
        : prev,
    );
    if (openDrawerMatch && openDrawerMatch.id === next.id) setOpenDrawerMatch(next);
    if (openEditMatch && openEditMatch.id === next.id) setOpenEditMatch(next);
  }

  async function bulkAutoFix() {
    if (!runId || !run) return;
    setBulkFixBusy(true);
    try {
      const byId = new Map(run.matches.map((m) => [m.id, m]));
      for (const id of fixableMatchIds) {
        const m = byId.get(id);
        if (!m || !canAuthorityAutoFix(m)) continue;
        const fix = getAuthoritySuggestedFix(m);
        const patch = fix ? resolveAuthorityFixPatch(m, fix) : null;
        if (!patch) continue;
        const updated = await Runs.editMatch(runId, id, patch);
        patchMatch(updated);
        try {
          await Runs.aiVerify(runId, id);
        } catch (err) {
          console.warn("Authority bulk auto-fix re-verify failed", err);
        }
      }
      await refresh();
    } finally {
      setBulkFixBusy(false);
    }
  }

  const enrichSourceSummary = enrichResult
    ? Object.entries(enrichResult.source_counts)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([source, count]) => `${source}: ${count}`)
        .join(" · ")
    : "";

  if (error)
    return <Layout><Glass className="p-6 text-danger">{error}</Glass></Layout>;
  if (!run)
    return <Layout><p className="muted">Loading run…</p></Layout>;

  return (
    <Layout>
      <div className="space-y-6">
        <Glass as="section" className="p-6 space-y-2">
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
            {" · "}
            <Link to={`/runs/${run.id}/linked-data-explorer`} className="hover:text-ink underline">
              Linked Data Explorer
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
          {run.error && <p className="text-danger text-sm">{run.error}</p>}
        </Glass>

        <Glass as="section" className="p-6 space-y-4">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <div className="kicker">Authority candidates</div>
              <h3 className="text-lg font-medium">
                Review &amp; approve · <span className="muted">{filteredMatchIds.length} of {run.matches.length}</span>
              </h3>
            </div>
            <div className="flex gap-2 items-center flex-wrap">
              <button disabled={selected.size === 0} onClick={() => bulk(true)}  className="button-primary !py-1.5 text-sm">Approve selected</button>
              <button disabled={selected.size === 0} onClick={() => bulk(false)} className="button-ghost   !py-1.5 text-sm">Unapprove selected</button>
              <button onClick={() => setShowAutoApprove(true)}
                      className="button-ghost !py-1.5 text-sm">
                Auto-approve…
              </button>
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
                        matchIds: filteredMatchIds,
                        label: `${filteredMatchIds.length} visible`,
                      })}
                      disabled={filteredMatchIds.length === 0}
                      className="button-ghost !py-1.5 text-sm text-biu-sky">
                ✨ Verify all visible with AI
              </button>
              <button
                type="button"
                disabled={fixableMatchIds.length === 0 || bulkFixBusy}
                onClick={() => { void bulkAutoFix(); }}
                title="Apply every visible AI-suggested fix with high confidence"
                className="button-ghost !py-1.5 text-sm text-warn"
              >
                {bulkFixBusy
                  ? "⏳ Applying fixes…"
                  : `✨ Auto-fix ${fixableMatchIds.length} high-confidence`}
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
                    className="button-ghost !py-0.5 !px-2 text-xs text-danger whitespace-nowrap">
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
                  ✓ checked {enrichResult.checked} · matched {enrichResult.matched} ·{" "}
                  {enrichResult.updated} updated · {enrichResult.newly_matched} new
                  {enrichSourceSummary && <> · {enrichSourceSummary}</>}
                  {enrichResult.skip_cache && <> · cache bypassed</>}
                </span>
              )}
            </div>
          </div>

          {/* Live re-enrichment progress panel */}
          {enrichPhase === "running" && (
            <Glass className="rounded-lg p-4 space-y-3 border border-biu-sky/20">
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
                        <GlassPill className="px-2 py-[1px] text-[10px] uppercase tracking-wider text-biu-sky shrink-0">
                          {enrichCurrentSource}
                        </GlassPill>
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
            </Glass>
          )}

          {/* Error banner */}
          {enrichPhase === "error" && enrichError && (
            <Glass variant="compact" className="rounded-lg p-3 border border-red-400/30 text-danger text-sm">
              ✕ Re-enrichment failed: {enrichError}
            </Glass>
          )}

          <AuthorityMatchingHelp />

          <AuthorityTable
            matches={run.matches}
            runId={runId!}
            projectId={run.project_id}
            noteIndex={noteIndex}
            selectedIds={selected}
            onSelectToggle={(id) => {
              setSelected((prev) => {
                const next = new Set(prev);
                if (next.has(id)) next.delete(id); else next.add(id);
                return next;
              });
            }}
            onApproveToggle={toggle}
            onOpenDrawer={setOpenDrawerMatch}
            onOpenEdit={setOpenEditMatch}
            onMatchChanged={patchMatch}
            onFilteredChange={setFilteredMatchIds}
            onFixableChange={setFixableMatchIds}
          />
        </Glass>
      </div>

      <AuthorityDetailDrawer
        match={openDrawerMatch}
        runId={runId!}
        projectId={run.project_id}
        onClose={() => setOpenDrawerMatch(null)}
        onMatchChanged={patchMatch}
      />

      {openEditMatch && (
        <AuthorityMatchEditDialog
          runId={runId!}
          match={openEditMatch}
          onClose={() => setOpenEditMatch(null)}
          onSaved={patchMatch}
        />
      )}

      {showAutoApprove && runId && (
        <AuthorityAutoApproveRuleBuilder
          runId={runId}
          visibleMatchIds={
            run && filteredMatchIds.length < run.matches.length
              ? filteredMatchIds
              : undefined
          }
          onClose={() => setShowAutoApprove(false)}
          onComplete={() => { setShowAutoApprove(false); void refresh(); }}
        />
      )}

      {verifyScope && runId && (
        <AiVerificationModal
          runId={runId}
          scopeKind={verifyScope.kind}
          matchIds={verifyScope.matchIds}
          scopeLabel={verifyScope.label}
          onComplete={() => { void refresh(); }}
          onClose={() => { setVerifyScope(null); void refresh(); }}
        />
      )}
    </Layout>
  );
}


// ── helpers ─────────────────────────────────────────────────────────────


function StatusPill({ status }: { status: string }) {
  const tone =
    status === "succeeded" ? "text-biu-sky"
    : status === "running" ? "text-warn"
    : status === "failed"  ? "text-danger"
    : "muted";
  return <GlassPill className={`px-3 py-1 text-[10px] kicker ${tone}`}>{status}</GlassPill>;
}
