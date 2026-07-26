import {useCallback, useEffect, useRef, useState} from "react";

import {ApiError} from "@/api/client";
import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {
  fetchAllStudioItems,
  type UploadOutcome,
  type UploadResponse,
  type WikidataUploadTarget,
} from "@/api/wikidataStudio";
import {WikidataVerify, type AgentEvent} from "@/api/wikidataVerify";
import {AgentFlowDiagram, makeInitialFlowState, type FlowState} from "@/components/AgentFlowDiagram";
import {VerdictsTable} from "@/components/VerdictsTable";
import {Glass, GlassPill} from "@/components/glass";
import {CuratorTableScroll} from "@/components/CuratorTableScroll";
import {JobProgressInline} from "@/components/jobs/JobProgressInline";
import {Tier1ModelSelect, useTier1Model} from "@/components/Tier1ModelSelect";
import {WikidataVerificationModal} from "@/components/wikidata/WikidataVerificationModal";
import {useRunJobAttachment} from "@/hooks/useRunJobAttachment";
import {useVerifyJob} from "@/hooks/useVerifyJob";
import {isJobActive, useRunJobs} from "@/stores/runJobs";
import {fetchVerifySessionWithJobFallback} from "@/utils/fetchVerifySession";
import {
  collectNewProgressOutcomes,
  type StudioUploadProgressOutcome,
} from "@/utils/studioUploadProgress";
import {hydrateVerifySession, mergeFlowWithJobProgress} from "@/utils/verifySessionHydrate";

export interface WikidataUploadPanelProps {
  runId: string;
  source: "legacy" | "canonical";
  approvedOnly: boolean;
  uploadApprovedOnly: boolean;
  buildPresent: boolean;
  refreshToken?: unknown;
  compact?: boolean;
  uploadTarget?: WikidataUploadTarget;
  onUploadTargetChange?: (target: WikidataUploadTarget) => void;
  onUploaded?: (meta: {
    upload_target: WikidataUploadTarget;
    moratorium_lifted: boolean;
    test_mode: boolean;
  }) => void;
  onUploadOutcomes?: (outcomes: StudioUploadProgressOutcome[]) => void;
}

function verdictOverall(ev: AgentEvent): string {
  const v = (ev.verdict ?? {}) as Record<string, unknown>;
  return String(v.overall ?? "unknown").toLowerCase();
}

function verdictLocalId(row: Record<string, unknown>): string {
  const cand = (row.candidate ?? {}) as Record<string, unknown>;
  return String(cand._item_id ?? cand._local_id ?? cand.local_id ?? "");
}

const TARGET_OPTIONS: Array<{
  value: WikidataUploadTarget;
  label: string;
  hint: string;
}> = [
  {
    value: "dry_run",
    label: "Moratorium active (dry-run)",
    hint: "Preview only — no public writes",
  },
  {
    value: "test",
    label: "Wikidata test",
    hint: "Write to test.wikidata.org",
  },
  {
    value: "live",
    label: "Live Wikidata",
    hint: "Write to wikidata.org",
  },
];

export function WikidataUploadPanel({
  runId,
  source,
  approvedOnly,
  uploadApprovedOnly,
  buildPresent,
  refreshToken,
  compact = false,
  uploadTarget: uploadTargetProp,
  onUploadTargetChange,
  onUploaded,
  onUploadOutcomes,
}: WikidataUploadPanelProps) {
  const [uploadTargetLocal, setUploadTargetLocal] = useState<WikidataUploadTarget>("dry_run");
  const uploadTarget = uploadTargetProp ?? uploadTargetLocal;
  const setUploadTarget = (next: WikidataUploadTarget) => {
    if (onUploadTargetChange) onUploadTargetChange(next);
    else setUploadTargetLocal(next);
  };
  const dryRun = uploadTarget === "dry_run";
  const [updateExisting, setUpdateExisting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResponse | null>(null);
  const [job, setJob] = useState<RunJobSnapshot | null>(null);
  const [lastTarget, setLastTarget] = useState<WikidataUploadTarget>("dry_run");

  const [preVerify, setPreVerify] = useState(false);
  const [postVerify, setPostVerify] = useState(false);
  const [verifyPhase, setVerifyPhase] = useState<"pre" | "post" | null>(null);
  const [verifyEvents, setVerifyEvents] = useState<AgentEvent[]>([]);
  const [verifyVerdicts, setVerifyVerdicts] = useState<Record<string, AgentEvent>>({});
  const [verifyFlow, setVerifyFlow] = useState<FlowState>(makeInitialFlowState());
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [failConfirm, setFailConfirm] = useState<{failed: number; total: number; ids: string[]} | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewIds, setReviewIds] = useState<string[]>([]);
  const {list: tier1List, tierModel, setTierModel, loading: tier1Loading} = useTier1Model();
  const seenOutcomeIdsRef = useRef<Set<string>>(new Set());

  const loadVerifySession = useCallback(async (sessionId: string, verifyJob?: RunJobSnapshot) => {
    const full = await fetchVerifySessionWithJobFallback(
      runId, sessionId, "wikidata_verify", WikidataVerify.session, verifyJob,
    );
    const hydrated = hydrateVerifySession(full, verdictLocalId);
    setVerifyEvents(hydrated.events);
    setVerifyVerdicts(hydrated.verdicts);
    setVerifyFlow(mergeFlowWithJobProgress(hydrated.flow, verifyJob?.progress));
  }, [runId]);

  const handleVerifyFailed = useCallback((msg: string) => {
    setVerifyError(msg);
    setVerifyPhase(null);
  }, []);

  const {running: verifyRunning, start: startVerifyJob} = useVerifyJob({
    runId,
    kind: "wikidata_verify",
    loadSession: loadVerifySession,
    onFailed: handleVerifyFailed,
  });

  const upsertJob = useRunJobs((s) => s.upsertJob);

  const {activeJob, setTrackedJobId, ensureJobPolling} = useRunJobAttachment(
    runId,
    "wikidata_upload",
    (j) => {
      setJob(j);
      const dry = Boolean(
        (j.params as {dry_run?: unknown} | null)?.dry_run
        || (j.params as {upload_target?: unknown} | null)?.upload_target === "dry_run",
      );
      if (isJobActive(j.status)) {
        setBusy(true);
        if (!dry) {
          const fresh = collectNewProgressOutcomes(j.progress, seenOutcomeIdsRef.current);
          if (fresh.length) onUploadOutcomes?.(fresh);
        }
      }
      if (j.status === "succeeded" && j.result) {
        const outcomes = (j.result.outcomes as UploadOutcome[]) ?? [];
        const target = (String(
          j.result.upload_target
          || (j.result.test_mode ? "test" : j.result.dry_run ? "dry_run" : "live"),
        )) as WikidataUploadTarget;
        const uploadResult: UploadResponse = {
          dry_run: Boolean(j.result.dry_run),
          upload_target: target,
          moratorium_lifted: Boolean(j.result.moratorium_lifted),
          test_mode: Boolean(j.result.test_mode),
          outcomes,
        };
        setResult(uploadResult);
        setLastTarget(target);
        setBusy(false);
        onUploaded?.({
          upload_target: target,
          moratorium_lifted: uploadResult.moratorium_lifted,
          test_mode: uploadResult.test_mode,
        });
        if (postVerify && !uploadResult.dry_run) {
          const scopeIds = outcomes
            .filter((o) => o.status === "success" || o.status === "updated" || o.status === "adopted" || o.status === "exists")
            .map((o) => o.local_id)
            .filter(Boolean);
          if (scopeIds.length > 0) {
            setVerifyError(null);
            setVerifyEvents([]);
            setVerifyVerdicts({});
            setVerifyFlow(makeInitialFlowState());
            setVerifyPhase("post");
            void startVerifyJob({
              action_id: "autofix_from_wikidata",
              item_ids: scopeIds,
              approved_only: approvedOnly,
              source,
              override_cache: false,
              tier_model: tierModel,
            });
          }
        }
      }
      if (j.status === "failed" || j.status === "cancelled") {
        setBusy(false);
        onUploaded?.({
          upload_target: uploadTarget,
          moratorium_lifted: true,
          test_mode: uploadTarget === "test",
        });
      }
    },
  );

  const doUpload = useCallback(async () => {
    if (uploadTarget === "live") {
      const ok = window.confirm(
        "Upload to live wikidata.org?\n\n"
        + "This writes real public items. Prefer dry-run or test.wikidata.org first.",
      );
      if (!ok) return;
    }
    setBusy(true);
    setError(null);
    try {
      seenOutcomeIdsRef.current = new Set();
      const started = await RunJobs.start(runId, "wikidata_upload", {
        upload_target: uploadTarget,
        dry_run: uploadTarget === "dry_run",
        approved_only: approvedOnly,
        source,
        item_approved_only: uploadApprovedOnly,
        update_existing: updateExisting,
      }).catch(async (e) => {
        if (e instanceof ApiError && e.status === 409) {
          const {jobs} = await RunJobs.listForRun(runId, true);
          const active = jobs.find((j) => j.kind === "wikidata_upload");
          if (active) return active;
        }
        throw e;
      });
      upsertJob(started);
      setJob(started);
      setTrackedJobId(started.id);
      ensureJobPolling();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
      setBusy(false);
    }
  }, [
    runId, uploadTarget, approvedOnly, uploadApprovedOnly, updateExisting, source,
    ensureJobPolling, setTrackedJobId, upsertJob,
  ]);

  useEffect(() => {
    setResult(null);
    setJob(null);
  }, [refreshToken]);

  useEffect(() => {
    if (verifyPhase !== "pre" || verifyRunning) return;
    const total = Object.keys(verifyVerdicts).length;
    if (total === 0) return;
    const failedIds = Object.entries(verifyVerdicts)
      .filter(([, ev]) => verdictOverall(ev) === "fail")
      .map(([id]) => id);
    setVerifyPhase(null);
    if (failedIds.length > 0) {
      setFailConfirm({failed: failedIds.length, total, ids: failedIds});
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
      const build = await fetchAllStudioItems(runId, {approvedOnly});
      const scopeIds = build.items
        .filter((i) => i.local_id && (!i.existing_qid || updateExisting))
        .map((i) => i.local_id as string);
      if (scopeIds.length === 0) {
        await doUpload();
        return;
      }
      setVerifyPhase("pre");
      await startVerifyJob({
        action_id: "audit_wikidata_item",
        item_ids: scopeIds,
        approved_only: approvedOnly,
        source,
        override_cache: false,
        tier_model: tierModel,
      });
    } catch (e) {
      setVerifyError(e instanceof ApiError ? e.detail : String(e));
      setVerifyPhase(null);
    }
  }, [approvedOnly, doUpload, runId, source, startVerifyJob, tierModel, updateExisting]);

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
  const canUpload = buildPresent;

  const pillTarget = result ? lastTarget : uploadTarget;
  const moratoriumPill = pillTarget === "dry_run"
    ? <GlassPill className="px-2 py-0.5 text-[10px] kicker text-warn" data-testid="wikidata-upload-target-pill">moratorium active (dry-run)</GlassPill>
    : pillTarget === "test"
      ? <GlassPill className="px-2 py-0.5 text-[10px] kicker text-biu-sky" data-testid="wikidata-upload-target-pill">TEST MODE (test.wikidata.org)</GlassPill>
      : <GlassPill className="px-2 py-0.5 text-[10px] kicker text-danger" data-testid="wikidata-upload-target-pill">LIVE (wikidata.org)</GlassPill>;

  const submitLabel = dryRun
    ? "Preview upload"
    : uploadTarget === "test"
      ? "Upload to test Wikidata"
      : "Upload to live Wikidata";

  const controls = (
    <>
      <div className="flex flex-wrap items-center gap-3">
        {moratoriumPill}
      </div>
      <fieldset
        className="flex flex-wrap gap-3 text-sm"
        disabled={busy || jobRunning || preVerifyRunning}
        data-testid="wikidata-upload-target"
      >
        <legend className="sr-only">Wikidata upload target</legend>
        {TARGET_OPTIONS.map((opt) => (
          <label
            key={opt.value}
            className="flex items-start gap-2 muted cursor-pointer max-w-xs"
            title={opt.hint}
          >
            <input
              type="radio"
              name="wikidata-upload-target"
              value={opt.value}
              checked={uploadTarget === opt.value}
              onChange={() => setUploadTarget(opt.value)}
              data-testid={`wikidata-upload-target-${opt.value}`}
              className="mt-1"
            />
            <span>
              <span className="text-ink">{opt.label}</span>
              <span className="block text-xs muted">{opt.hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1 text-sm muted" title="Update items that already have a Wikidata QID">
          <input
            type="checkbox"
            checked={updateExisting}
            onChange={(e) => setUpdateExisting(e.target.checked)}
            disabled={busy || jobRunning || preVerifyRunning}
            data-testid="wikidata-upload-update-existing"
          />
          Update records already on Wikidata
        </label>
        <button
          onClick={handleUploadClick}
          disabled={busy || jobRunning || preVerifyRunning || !canUpload}
          className={dryRun ? "button-ghost text-sm" : uploadTarget === "live" ? "button-primary text-sm" : "button-ghost text-sm"}
          data-testid="wikidata-upload-submit"
        >
          {busy || jobRunning || preVerifyRunning
            ? preVerifyRunning
              ? "Verifying with AI…"
              : dryRun
                ? "Previewing…"
                : "Uploading…"
            : submitLabel}
        </button>
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
        <label className="flex items-center gap-1" title="Runs audit_wikidata_item before upload starts">
          <input
            type="checkbox"
            checked={preVerify}
            onChange={(e) => setPreVerify(e.target.checked)}
            disabled={busy || jobRunning || preVerifyRunning}
            data-testid="wikidata-upload-preverify-checkbox"
          />
          Verify with AI before upload
        </label>
        <label className="flex items-center gap-1" title="After live/test upload, runs autofix_from_wikidata on written items">
          <input
            type="checkbox"
            checked={postVerify}
            onChange={(e) => setPostVerify(e.target.checked)}
            disabled={busy || jobRunning || preVerifyRunning || dryRun}
            data-testid="wikidata-upload-postverify-checkbox"
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
        <div className="rounded-lg border border-warn/40 bg-warn/5 p-3 text-sm space-y-2" data-testid="wikidata-upload-failconfirm">
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
              data-testid="wikidata-upload-failconfirm-review"
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
              data-testid="wikidata-upload-failconfirm-anyway"
            >
              Upload anyway
            </button>
          </div>
        </div>
      )}

      {(verifyPhase === "pre" || verifyPhase === "post") && (
        <div className="space-y-2 border-t border-white/5 pt-3" data-testid={`wikidata-upload-verify-phase-${verifyPhase}`}>
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

      {result && <UploadResultSummary result={result} />}

      {reviewOpen && (
        <WikidataVerificationModal
          runId={runId}
          scopeKind="selection"
          itemIds={reviewIds}
          scopeLabel={`${reviewIds.length} flagged item${reviewIds.length === 1 ? "" : "s"}`}
          source={source}
          approvedOnly={approvedOnly}
          onClose={() => setReviewOpen(false)}
        />
      )}
    </>
  );

  if (compact) {
    return (
      <div className="space-y-3" data-testid="wikidata-item-upload-actions">
        {error && <p className="text-sm text-danger">{error}</p>}
        {!canUpload && <p className="text-sm muted">Build items before uploading.</p>}
        {controls}
        {extras}
      </div>
    );
  }

  return (
    <Glass as="section" className="p-6 space-y-3" data-testid="wikidata-upload-panel">
      <div>
        <div className="kicker">Upload to Wikidata</div>
        <h3 className="text-lg font-medium">Reconcile-before-create upload</h3>
        <p className="muted text-sm leading-relaxed mt-1">
          Default is dry-run (moratorium active). Choose Wikidata test or live when you are ready to write.
        </p>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      {!canUpload && <p className="text-sm muted">Build items above before uploading.</p>}
      {controls}
      {extras}
    </Glass>
  );
}

function UploadResultSummary({result}: {result: UploadResponse}) {
  const [expand, setExpand] = useState(false);
  const adopted = result.outcomes.filter((o) => o.status === "adopted").length;
  const blocked = result.outcomes.filter((o) => o.status === "blocked" || o.status === "skipped").length;
  const failed = result.outcomes.filter((o) => o.status === "failed").length;
  const created = result.outcomes.filter((o) => o.status === "success" || o.status === "pending").length;
  const updated = result.outcomes.filter((o) => o.status === "updated" || o.status === "exists").length;

  return (
    <div className="border-t border-white/5 pt-3 space-y-2">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <p className="text-sm">
          <span className="muted">{result.dry_run ? "Would create:" : "Created:"}</span>{" "}
          <b className="text-biu-sky">{created}</b>
          {" · "}
          <span className="muted">{result.dry_run ? "would update " : "updated "}</span>
          <b className="text-biu-sky">{updated}</b>
          {adopted > 0 && (
            <>
              {" · adopted "}
              <b className="text-biu-sky">{adopted}</b>
            </>
          )}
          {blocked > 0 && (
            <>
              {" · "}
              <span className="text-warn">blocked/skipped {blocked}</span>
            </>
          )}
          {failed > 0 && (
            <>
              {" · "}
              <span className="text-danger">failed {failed}</span>
            </>
          )}
        </p>
        <button onClick={() => setExpand((v) => !v)} className="button-ghost text-xs">
          {expand ? "Hide details" : "Show details"}
        </button>
      </div>
      {expand && (
        <CuratorTableScroll>
          <table className="w-full text-sm">
            <thead className="bg-white/3 text-xs uppercase muted tracking-wider sticky top-0 z-10 table-head">
              <tr>
                {["Local id", "Status", "QID", "Message"].map((h) => (
                  <th key={h} className="text-left px-3 py-2">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.outcomes.map((o) => (
                <tr key={o.local_id} className="border-t border-white/5">
                  <td className="px-3 py-2 font-mono text-xs">{o.local_id}</td>
                  <td className="px-3 py-2"><GlassPill className="px-2 py-0.5 text-[10px] kicker">{o.status}</GlassPill></td>
                  <td className="px-3 py-2 font-mono text-xs">{o.qid ?? "—"}</td>
                  <td className="px-3 py-2 text-xs">{o.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CuratorTableScroll>
      )}
    </div>
  );
}
