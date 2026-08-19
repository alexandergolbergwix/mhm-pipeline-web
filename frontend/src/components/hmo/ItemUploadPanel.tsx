import {useCallback, useEffect, useMemo, useRef, useState} from "react";

import { ApiError } from "@/api/client";
import {
  HmoStudio,
  isItemUploadJob,
  itemUploadResultFromJob,
  type HmoItemStatus,
  type HmoItemUploadResult,
} from "@/api/hmoStudio";
import { HmoItemVerify } from "@/api/hmoItemVerify";
import { HmoStudioItems } from "@/api/hmoStudioItems";
import type { RunJobSnapshot } from "@/api/runJobs";
import type { AgentEvent } from "@/api/wikidataVerify";
import { AgentFlowDiagram, makeInitialFlowState, type FlowState } from "@/components/AgentFlowDiagram";
import { VerdictsTable } from "@/components/VerdictsTable";
import { Glass, GlassPill } from "@/components/glass";
import {CuratorTableScroll} from "@/components/CuratorTableScroll";
import { HmoItemVerificationModal } from "@/components/hmo/HmoItemVerificationModal";
import { JobProgressInline } from "@/components/jobs/JobProgressInline";
import {Tier1ModelSelect, useTier1Model} from "@/components/Tier1ModelSelect";
import { useRunJobAttachment } from "@/hooks/useRunJobAttachment";
import { useVerifyJob } from "@/hooks/useVerifyJob";
import {isJobActive, useRunJobs} from "@/stores/runJobs";
import { fetchVerifySessionWithJobFallback } from "@/utils/fetchVerifySession";
import {
  collectNewProgressOutcomes,
  type StudioUploadProgressOutcome,
} from "@/utils/studioUploadProgress";
import { hydrateVerifySession, mergeFlowWithJobProgress } from "@/utils/verifySessionHydrate";

interface ItemUploadPanelProps {
  runId: string;
  wikibaseConfigured: boolean;
  /** Bump this to force a status refresh after a sibling ItemBuildPanel builds. */
  refreshToken?: unknown;
  /** Toolbar row for the review panel; upload progress/results still render below. */
  compact?: boolean;
  /** local_ids with Last push = failed (survives refresh when result panel is cleared). */
  failedLocalIds?: string[];
  /** Full reload after terminal upload (or non-job sync path). */
  onUploaded?: () => void;
  /** Patch only the rows that just finished uploading (mid-run, no flicker). */
  onUploadOutcomes?: (outcomes: StudioUploadProgressOutcome[]) => void;
}

function verdictOverall(ev: AgentEvent): string {
  const v = (ev.verdict ?? {}) as Record<string, unknown>;
  return String(v.overall ?? "unknown").toLowerCase();
}

function verdictLocalId(row: Record<string, unknown>): string {
  const cand = (row.candidate ?? {}) as Record<string, unknown>;
  return String(cand._local_id ?? cand.local_id ?? "");
}

/**
 * Phase 5: create-only, two-pass upload of the run's most recent item
 * build. Disabled until a build exists.
 */
export function ItemUploadPanel({
  runId,
  wikibaseConfigured,
  refreshToken,
  compact = false,
  failedLocalIds = [],
  onUploaded,
  onUploadOutcomes,
}: ItemUploadPanelProps) {
  const [status, setStatus] = useState<HmoItemStatus | null>(null);
  const [result, setResult] = useState<HmoItemUploadResult | null>(null);
  const [job, setJob] = useState<RunJobSnapshot | null>(null);
  const [dryRun, setDryRun] = useState(true);
  const [updateExisting, setUpdateExisting] = useState(false);
  const [allowShaclErrors, setAllowShaclErrors] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [preVerify, setPreVerify] = useState(false);
  const [postVerify, setPostVerify] = useState(false);
  const [verifyPhase, setVerifyPhase] = useState<"pre" | "post" | null>(null);
  const [verifyEvents, setVerifyEvents] = useState<AgentEvent[]>([]);
  const [verifyVerdicts, setVerifyVerdicts] = useState<Record<string, AgentEvent>>({});
  const [verifyFlow, setVerifyFlow] = useState<FlowState>(makeInitialFlowState());
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [failConfirm, setFailConfirm] = useState<{ failed: number; total: number; ids: string[] } | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewIds, setReviewIds] = useState<string[]>([]);
  const {list: tier1List, tierModel, setTierModel, loading: tier1Loading} = useTier1Model();
  const upsertJob = useRunJobs((s) => s.upsertJob);
  const notifiedSuccessIdRef = useRef<string | null>(null);
  const seenOutcomeIdsRef = useRef<Map<string, string>>(new Map());

  const refresh = useCallback(async () => {
    try {
      setStatus(await HmoStudio.itemStatus(runId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, [runId]);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshToken]);

  const loadVerifySession = useCallback(async (sessionId: string, job?: RunJobSnapshot) => {
    const full = await fetchVerifySessionWithJobFallback(
      runId, sessionId, "hmo_item_verify", HmoItemVerify.session, job,
    );
    const hydrated = hydrateVerifySession(full, verdictLocalId);
    setVerifyEvents(hydrated.events);
    setVerifyVerdicts(hydrated.verdicts);
    setVerifyFlow(mergeFlowWithJobProgress(hydrated.flow, job?.progress));
  }, [runId]);

  const handleVerifyFailed = useCallback((msg: string) => {
    setVerifyError(msg);
    setVerifyPhase(null);
  }, []);

  const { running: verifyRunning, start: startVerifyJob } = useVerifyJob({
    runId,
    kind: "hmo_item_verify",
    loadSession: loadVerifySession,
    onFailed: handleVerifyFailed,
  });

  const {activeJob, setTrackedJobId, ensureJobPolling} = useRunJobAttachment(
    runId,
    "hmo_item_upload",
    (j) => {
      setJob(j);
      if (isJobActive(j.status)) {
        setBusy(true);
        const fresh = collectNewProgressOutcomes(j.progress, seenOutcomeIdsRef.current);
        if (fresh.length) onUploadOutcomes?.(fresh);
      }
      if (j.status === "succeeded") {
        const fromJob = itemUploadResultFromJob(j);
        if (fromJob) setResult(fromJob);
        void refresh();
        setBusy(false);
        if (notifiedSuccessIdRef.current !== j.id) {
          notifiedSuccessIdRef.current = j.id;
          onUploaded?.();
        }
        if (postVerify && fromJob && !fromJob.dry_run) {
          const scopeIds = fromJob.outcomes
            .filter((o) => o.status === "created" || o.status === "updated" || o.status === "adopted")
            .map((o) => o.local_id);
          if (scopeIds.length > 0) {
            setVerifyError(null);
            setVerifyEvents([]);
            setVerifyVerdicts({});
            setVerifyFlow(makeInitialFlowState());
            setVerifyPhase("post");
            void startVerifyJob({
              action_id: "autofix_hmo_wikibase_item",
              item_ids: scopeIds,
              override_cache: false,
              tier_model: tierModel,
            });
          }
        }
      }
      if (j.status === "failed" || j.status === "cancelled") {
        void refresh();
        setBusy(false);
        onUploaded?.();
      }
    },
  );

  const doUpload = useCallback(async (
    localIds?: string[],
    opts?: {dryRun?: boolean},
  ) => {
    const useDryRun = opts?.dryRun ?? dryRun;
    setBusy(true);
    setError(null);
    try {
      const r = await HmoStudio.uploadItems(
        runId, useDryRun, updateExisting, allowShaclErrors, localIds,
      );
      if (isItemUploadJob(r)) {
        notifiedSuccessIdRef.current = null;
        seenOutcomeIdsRef.current = new Map();
        upsertJob(r);
        setJob(r);
        setTrackedJobId(r.id);
        ensureJobPolling();
        if (!isJobActive(r.status)) {
          setBusy(false);
        }
      } else {
        setResult(r);
        await refresh();
        onUploaded?.();
        setBusy(false);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
      setBusy(false);
    }
  }, [
    runId, dryRun, updateExisting, allowShaclErrors, refresh, upsertJob,
    setTrackedJobId, ensureJobPolling, onUploaded,
  ]);

  const retryFailedIds = useMemo(() => {
    const fromResult = (result?.outcomes ?? [])
      .filter((o) => o.status === "failed")
      .map((o) => o.local_id)
      .filter(Boolean);
    if (fromResult.length > 0) return fromResult;
    return failedLocalIds;
  }, [result, failedLocalIds]);

  const handleRetryFailed = useCallback(() => {
    if (!retryFailedIds.length) return;
    // Failures only come from live writes — always retry live.
    setDryRun(false);
    void doUpload(retryFailedIds, {dryRun: false});
  }, [doUpload, retryFailedIds]);

  useEffect(() => {
    if (verifyPhase !== "pre" || verifyRunning) return;
    const total = Object.keys(verifyVerdicts).length;
    if (total === 0) return;
    const failedIds = Object.entries(verifyVerdicts)
      .filter(([, ev]) => verdictOverall(ev) === "fail")
      .map(([id]) => id);
    setVerifyPhase(null);
    if (failedIds.length > 0) {
      setFailConfirm({ failed: failedIds.length, total, ids: failedIds });
    } else {
      void doUpload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [verifyPhase, verifyRunning, verifyVerdicts]);

  const startPreVerify = useCallback(async () => {
    setVerifyError(null);
    setVerifyEvents([]);
    setVerifyVerdicts({});
    setVerifyFlow(makeInitialFlowState());
    setFailConfirm(null);
    try {
      const { items } = await HmoStudioItems.list(runId);
      const scopeIds = items.filter((i) => i.status === "would_create").map((i) => i.local_id);
      if (scopeIds.length === 0) {
        await doUpload();
        return;
      }
      setVerifyPhase("pre");
      await startVerifyJob({
        action_id: "audit_hmo_wikibase_item",
        item_ids: scopeIds,
        override_cache: false,
        tier_model: tierModel,
      });
    } catch (e) {
      setVerifyError(e instanceof ApiError ? e.detail : String(e));
      setVerifyPhase(null);
    }
  }, [runId, doUpload, startVerifyJob, tierModel]);

  const handleUploadClick = useCallback(() => {
    setError(null);
    setFailConfirm(null);
    if (preVerify) {
      void startPreVerify();
    } else {
      void doUpload();
    }
  }, [preVerify, startPreVerify, doUpload]);

  const liveJob = activeJob ?? job;
  const jobRunning = liveJob?.status === "queued" || liveJob?.status === "running";
  const preVerifyRunning = verifyPhase === "pre" && verifyRunning;
  const canUpload = !!status?.build_present;

  const controls = (
    <>
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1 text-sm muted" title="Show the entries that would be published without changing the live catalogue.">
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            disabled={busy || jobRunning || preVerifyRunning}
            data-testid="hmo-upload-dry-run"
          />
          Preview only
        </label>
        <label
          className="flex items-center gap-1 text-sm muted"
          title="Update entries that have already been published in the HMO catalogue."
        >
          <input
            type="checkbox"
            checked={updateExisting}
            onChange={(e) => setUpdateExisting(e.target.checked)}
            disabled={busy || jobRunning || preVerifyRunning}
            data-testid="hmo-upload-update-existing"
          />
          Update published entries
        </label>
        <details className="text-sm muted">
          <summary className="cursor-pointer">Advanced publication checks</summary>
          <label className="mt-2 flex items-center gap-1" title="When enabled, entries with blocking validation errors can still be sent to the live catalogue.">
            <input
              type="checkbox"
              checked={allowShaclErrors}
              onChange={(e) => setAllowShaclErrors(e.target.checked)}
              disabled={busy || jobRunning || preVerifyRunning}
              data-testid="hmo-upload-allow-shacl-checkbox"
            />
            Allow entries with blocking validation errors
          </label>
        </details>
        <button
          onClick={handleUploadClick}
          disabled={busy || jobRunning || preVerifyRunning || !canUpload || (!dryRun && !wikibaseConfigured)}
          className={dryRun ? "button-ghost text-sm" : "button-primary text-sm"}
          data-testid="hmo-upload-submit"
        >
          {busy || jobRunning || preVerifyRunning
            ? preVerifyRunning
              ? "Verifying with AI…"
              : dryRun
                ? "Previewing…"
                : "Publishing…"
            : dryRun
              ? "Preview upload"
              : "Publish approved entries"}
        </button>
        {!dryRun && !wikibaseConfigured && (
          <span className="text-xs text-warn">
            Wikibase Cloud is not configured on this server — contact an admin.
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-4 text-xs muted">
        {(preVerify || postVerify) && (
          <Tier1ModelSelect
            list={tier1List}
            loading={tier1Loading}
            tierModel={tierModel}
            onChange={setTierModel}
            disabled={busy || jobRunning || preVerifyRunning}
          />
        )}
        <label
          className="flex items-center gap-1"
          title="Runs the audit_hmo_wikibase_item evaluator (Gemini, cached per item) over the not-yet-uploaded items before the upload starts. Rate-limited to 60 requests/min; cached verdicts are free on repeat runs."
        >
          <input
            type="checkbox"
            checked={preVerify}
            onChange={(e) => setPreVerify(e.target.checked)}
            disabled={busy || jobRunning || preVerifyRunning}
            data-testid="hmo-upload-preverify-checkbox"
          />
          Verify with AI before upload
        </label>
        <label
          className="flex items-center gap-1"
          title="After a successful upload, runs the autofix_hmo_wikibase_item evaluator (Gemini, cached per item) over the just-written items, comparing the live Wikibase entity against the build and proposing fixes. Rate-limited to 30 requests/min."
        >
          <input
            type="checkbox"
            checked={postVerify}
            onChange={(e) => setPostVerify(e.target.checked)}
            disabled={busy || jobRunning || preVerifyRunning}
            data-testid="hmo-upload-postverify-checkbox"
          />
          Verify with AI after upload (autofix)
        </label>
      </div>
    </>
  );

  const extras = (
    <>
      {verifyError && <p className="text-sm text-danger">{verifyError}</p>}

      {failConfirm && (
        <div className="rounded-lg border border-warn/40 bg-warn/5 p-3 text-sm space-y-2" data-testid="hmo-upload-failconfirm">
          <p>
            <b className="text-warn">{failConfirm.failed}</b> of {failConfirm.total} items failed the
            pre-upload AI audit. Review them, or proceed anyway — nothing is applied automatically.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              className="button-ghost text-xs"
              onClick={() => {
                setReviewIds(failConfirm.ids);
                setReviewOpen(true);
                setFailConfirm(null);
              }}
              data-testid="hmo-upload-failconfirm-review"
            >
              Review flagged items
            </button>
            <button
              type="button"
              className="button-primary text-xs"
              onClick={() => {
                setFailConfirm(null);
                void doUpload();
              }}
              data-testid="hmo-upload-failconfirm-anyway"
            >
              Upload anyway
            </button>
          </div>
        </div>
      )}

      {(verifyPhase === "pre" || verifyPhase === "post") && (
        <div className="space-y-2 border-t border-white/5 pt-3" data-testid={`hmo-upload-verify-phase-${verifyPhase}`}>
          <div className="kicker">
            {verifyPhase === "pre" ? "Pre-upload AI audit" : "Post-upload AI verification (autofix suggestions)"}
          </div>
          <AgentFlowDiagram lastEvent={verifyEvents[verifyEvents.length - 1] ?? null} flow={verifyFlow} />
          <VerdictsTable verdicts={verifyVerdicts} />
        </div>
      )}

      {liveJob && (
        <JobProgressInline
          job={liveJob}
          labels={{
            running: "Uploading…",
            succeeded: "Upload complete:",
            failed: "Upload failed:",
            cancelled: "Upload cancelled:",
          }}
        />
      )}

      {result && (
        <UploadResultSummary
          result={result}
          retryFailedCount={retryFailedIds.length}
          onRetryFailed={handleRetryFailed}
          retryDisabled={busy || jobRunning || preVerifyRunning || (!dryRun && !wikibaseConfigured)}
        />
      )}
      {!result && retryFailedIds.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="hmo-upload-retry-failed-banner">
          <span className="text-danger">
            {retryFailedIds.length} item{retryFailedIds.length === 1 ? "" : "s"} failed on the last push.
          </span>
          <button
            type="button"
            className="button-primary text-xs"
            disabled={busy || jobRunning || preVerifyRunning || (!dryRun && !wikibaseConfigured)}
            data-testid="hmo-upload-retry-failed"
            onClick={handleRetryFailed}
          >
            Retry {retryFailedIds.length} failed
          </button>
        </div>
      )}

      {reviewOpen && (
        <HmoItemVerificationModal
          runId={runId}
          scopeLabel={`${reviewIds.length} flagged item${reviewIds.length === 1 ? "" : "s"}`}
          itemIds={reviewIds}
          onClose={() => setReviewOpen(false)}
        />
      )}
    </>
  );

  if (compact) {
    return (
      <div className="space-y-3" data-testid="hmo-item-upload-actions">
        {error && <p className="text-sm text-danger whitespace-pre-wrap">{error}</p>}
        {!canUpload && (
          <p className="text-sm muted">Build items before uploading.</p>
        )}
        {controls}
        {extras}
      </div>
    );
  }

  return (
    <Glass as="section" className="p-6 space-y-3">
      <div>
        <div className="kicker">Upload to Wikibase Cloud</div>
        <h3 className="text-lg font-medium">Create-or-update, two-pass upload</h3>
        <p className="muted text-sm leading-relaxed mt-1">
          Pass 1 creates every not-yet-uploaded item with its literal
          claims. Pass 2 links item-to-item claims once both ends have
          real Wikibase ids. Already-uploaded items are skipped by
          default — enable &quot;Reupload (update existing)&quot; to
          refresh their labels/descriptions and merge in any new claims
          instead (a statement you added by hand on the wiki is never
          removed).
        </p>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      {!canUpload && (
        <p className="text-sm muted">Build items above before uploading.</p>
      )}

      {controls}
      {extras}
    </Glass>
  );
}

function UploadResultSummary({
  result,
  retryFailedCount = 0,
  onRetryFailed,
  retryDisabled = false,
}: {
  result: HmoItemUploadResult;
  retryFailedCount?: number;
  onRetryFailed?: () => void;
  retryDisabled?: boolean;
}) {
  const [expand, setExpand] = useState(false);
  return (
    <div className="border-t border-white/5 pt-3 space-y-2" data-testid="hmo-upload-result">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <p className="text-sm">
          <span className="muted">{result.dry_run ? "Would create:" : "Created:"}</span>{" "}
          <b className="text-biu-sky">{result.created}</b>
          {result.updated > 0 && (
            <>
              {" · "}
              <span className="muted">{result.dry_run ? "would update " : "updated "}</span>
              <b className="text-biu-sky">{result.updated}</b>
            </>
          )}
          {" · linked "}
          <b className="text-biu-sky">{result.linked}</b>
          {" · skipped "}
          {result.skipped}
          {result.unresolved_links > 0 && (
            <>
              {" · "}
              <span className="text-warn">unresolved links {result.unresolved_links}</span>
            </>
          )}
          {result.failed > 0 && (
            <>
              {" · "}
              <span className="text-danger">failed {result.failed}</span>
            </>
          )}
          {result.blocked > 0 && (
            <>
              {" · "}
              <span className="text-warn">
                {result.dry_run ? "would block " : "blocked "}
                {result.blocked}
              </span>
            </>
          )}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {retryFailedCount > 0 && onRetryFailed && (
            <button
              type="button"
              className="button-primary text-xs"
              disabled={retryDisabled}
              data-testid="hmo-upload-retry-failed"
              title="Upload only the items that failed on this run (plus deferred links that touch them)."
              onClick={onRetryFailed}
            >
              Retry {retryFailedCount} failed
            </button>
          )}
          <button onClick={() => setExpand((v) => !v)} className="button-ghost text-xs">
            {expand ? "Hide details" : "Show details"}
          </button>
        </div>
      </div>
      {expand && (
        <div className="space-y-3">
          <OutcomeTable
            title="Items"
            rows={result.outcomes.map((o) => ({
              key: o.local_id,
              cols: [o.local_id, o.status, o.wikibase_id ?? "—", o.message],
            }))}
            headers={["Local id", "Status", "Wikibase id", "Message"]}
            pillIndex={1}
          />
          {result.link_outcomes.length > 0 && (
            <OutcomeTable
              title="Deferred links"
              rows={result.link_outcomes.map((o, i) => ({
                key: `${o.source_local_id}-${o.property_id}-${i}`,
                cols: [o.source_local_id, o.property_id, o.target_local_id, o.status],
              }))}
              headers={["Source", "Property", "Target", "Status"]}
              pillIndex={3}
            />
          )}
        </div>
      )}
    </div>
  );
}

function OutcomeTable({
  title,
  headers,
  rows,
  pillIndex,
}: {
  title: string;
  headers: string[];
  rows: { key: string; cols: string[] }[];
  pillIndex: number;
}) {
  return (
    <div>
      <div className="text-xs muted mb-1">{title}</div>
      <CuratorTableScroll>
        <table className="w-full text-sm">
          <thead className="bg-white/3 text-xs uppercase muted tracking-wider sticky top-0 z-10 table-head">
            <tr>
              {headers.map((h) => (
                <th key={h} className="text-left px-3 py-2">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-white/5">
                {row.cols.map((c, i) => (
                  <td
                    key={i}
                    className={`px-3 py-2 text-xs ${i === 0 ? "font-mono" : ""}`}
                  >
                    {i === pillIndex ? <GlassPill className="px-2 py-0.5 text-[10px] kicker">{c}</GlassPill> : c}
                  </td>
                ))}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={headers.length} className="px-3 py-4 text-center muted text-sm">
                  None.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </CuratorTableScroll>
    </div>
  );
}
