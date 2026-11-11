import {useCallback, useEffect, useState} from "react";

import {ApiError} from "@/api/client";
import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import type {UploadOutcome} from "@/api/wikidataStudio";
import {CuratorTableScroll} from "@/components/CuratorTableScroll";
import {WikidataUploadSteps} from "@/components/wikidata/WikidataUploadSteps";
import {useRunJobAttachment} from "@/hooks/useRunJobAttachment";
import {useAuth} from "@/stores/auth";
import {isJobActive, selectActiveJob, useRunJobs} from "@/stores/runJobs";
import {tallyWikidataUploadOutcomeCounts} from "@/utils/wikidataUploadOutcomes";

export interface LinkOutcome {
  source_local_id: string;
  property_id: string;
  target_local_id: string;
  status: string;
  message: string;
  qid: string | null;
}

interface Props {
  runId: string;
  source: "legacy" | "canonical";
  approvedOnly: boolean;
  itemApprovedOnly: boolean;
  buildPresent: boolean;
}

export function WikidataOwnerPreview({runId, source, approvedOnly, itemApprovedOnly, buildPresent}: Props) {
  const [job, setJob] = useState<RunJobSnapshot | null>(null);
  const [starting, setStarting] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const userId = useAuth((s) => s.user?.id);
  const upsertJob = useRunJobs((s) => s.upsertJob);
  const jobsRecord = useRunJobs((s) => s.jobs);
  const anyActiveUpload = selectActiveJob(jobsRecord, runId, "wikidata_upload");
  const acceptJob = useCallback((snapshot: RunJobSnapshot) => Boolean(userId)
    && snapshot.created_by === userId && snapshot.run_id === runId && snapshot.kind === "wikidata_upload"
    && snapshot.params.owner_only === true && snapshot.params.dry_run === true
    && snapshot.params.upload_target === "dry_run" && snapshot.params.update_existing === true,
  [userId, runId]);
  const {setTrackedJobId, ensureJobPolling} = useRunJobAttachment(
    runId,
    "wikidata_upload",
    (snapshot) => {
      setJob(snapshot);
    },
    {acceptJob, autoAttach: false, includeResult: true},
  );

  useEffect(() => {
    setJob(null);
    setTrackedJobId(null);
    setError(null);
    if (!userId) {
      setRestoring(false);
      return;
    }
    let cancelled = false;
    setRestoring(true);
    void RunJobs.listForRun(runId, false, {kind: "wikidata_upload", limit: 100, includeResult: false}).then(async ({jobs}) => {
      if (cancelled) return;
      const latest = jobs.find(acceptJob);
      if (!latest) return;
      const detail = await RunJobs.get(runId, latest.id);
      if (cancelled || !acceptJob(detail)) return;
      setJob(detail);
      upsertJob(detail);
      if (isJobActive(detail.status)) {
        setTrackedJobId(detail.id);
        ensureJobPolling();
      }
    }).catch((caught: unknown) => {
      if (!cancelled) setError(caught instanceof ApiError ? caught.detail : String(caught));
    }).finally(() => {
      if (!cancelled) setRestoring(false);
    });
    return () => { cancelled = true; };
  }, [runId, userId, acceptJob, upsertJob, setTrackedJobId, ensureJobPolling]);

  const previewJob = job && acceptJob(job) ? job : null;
  const running = previewJob != null && isJobActive(previewJob.status);
  const uploadBusy = anyActiveUpload != null;
  const outcomes = Array.isArray(previewJob?.result?.outcomes) ? previewJob.result.outcomes as UploadOutcome[] : [];
  const links = Array.isArray(previewJob?.result?.link_outcomes) ? previewJob.result.link_outcomes as LinkOutcome[] : [];
  const counts = tallyWikidataUploadOutcomeCounts(outcomes);

  const start = async () => {
    setStarting(true);
    setError(null);
    setJob(null);
    try {
      const started = await RunJobs.start(runId, "wikidata_upload", {
        owner_only: true,
        upload_target: "dry_run",
        dry_run: true,
        update_existing: true,
        approved_only: approvedOnly,
        item_approved_only: itemApprovedOnly,
        source,
      });
      if (!acceptJob(started)) {
        throw new Error("The preview job identity or parameters could not be verified. Refresh before trying again.");
      }
      setJob(started);
      upsertJob(started);
      setTrackedJobId(started.id);
      ensureJobPolling();
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 409
        ? "Another Wikidata upload job is active. Wait for it to finish and try again."
        : caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (!job || !acceptJob(job) || !isJobActive(job.status)) return;
    setError(null);
    try {
      const snapshot = await RunJobs.cancel(runId, job.id);
      if (!acceptJob(snapshot) || snapshot.id !== job.id) throw new Error("The cancellation response did not match the preview job.");
      setJob(snapshot);
      upsertJob(snapshot);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : String(caught));
    }
  };

  return (
    <section className="border-t border-white/5 pt-4 space-y-3" data-testid="wikidata-owner-preview">
      <div>
        <div className="kicker">Owner-only preview</div>
        <p className="text-sm muted">
          Check existing items against the saved live Wikidata credential used for the preview. This preview makes no live writes
          and does not record upload status. It does not approve or authorize publication.
          It checks whether the credential owner created each existing target. It does not create items or accept foreign updates.
          The current source and approval filters apply; table filters do not. The job service retains the preview results.
        </p>
      </div>
      <div className="flex flex-wrap gap-2 items-center">
        <button type="button" className="button-ghost text-sm" onClick={() => void start()}
          disabled={!buildPresent || !userId || restoring || starting || uploadBusy || running} data-testid="wikidata-owner-preview-start">
          {starting || running ? "Checking owner-only eligibility..." : "Preview owner-only updates"}
        </button>
        {running && <button type="button" className="button-ghost text-sm" onClick={() => void cancel()}>
          Cancel preview
        </button>}
      </div>
      {!userId && <p className="text-xs muted">The user identity is unavailable. The preview cannot attach a job.</p>}
      {uploadBusy && !running && <p className="text-xs muted">Another Wikidata upload job is active. Wait for it to finish.</p>}
      {error && <p className="text-sm text-danger" role="alert">{error}</p>}
      {previewJob && <div className="space-y-2" aria-live="polite">
        <p className="text-sm">Preview status: {previewJob.status}</p>
        <WikidataUploadSteps progress={previewJob.progress ?? {}} preview />
        {previewJob.error && <p className="text-sm text-danger" role="alert">{previewJob.error}</p>}
        {previewJob.result && <>
          {previewJob.status !== "succeeded" && <p className="text-sm text-warn">This preview did not finish. The results are incomplete.</p>}
          <p className="text-xs muted">
            Preview scope: {String(previewJob.params.source ?? "canonical")} source;
            {previewJob.params.approved_only === true ? " approved authority and NER data" : " all authority and NER data"};
            {previewJob.params.item_approved_only === true ? " approved items only" : " all items"}.
          </p>
          <p className="text-sm" data-testid="wikidata-owner-preview-counts">
            Eligible to attempt update: {counts.updated} | Blocked: {counts.blocked} | Skipped: {counts.skipped} | Failed: {counts.failed}
          </p>
          <p className="text-xs muted">Eligibility is a preview, not a guarantee of a future update.</p>
          {outcomes.length === 0 ? <p className="text-sm muted">No item outcomes.</p> :
            <CuratorTableScroll className="max-h-[min(50vh,28rem)]">
              <table className="w-full text-sm" data-testid="wikidata-owner-preview-table">
                <thead className="text-xs uppercase muted"><tr>
                  {["Local ID", "Label", "Type", "QID", "Status", "Reason"].map((label) => <th key={label} className="text-left px-3 py-2">{label}</th>)}
                </tr></thead>
                <tbody>{outcomes.map((outcome, index) => <tr key={`${outcome.local_id}:${index}`} className="border-t border-white/5">
                  <td className="px-3 py-2">{outcome.local_id}</td><td className="px-3 py-2">{outcome.label}</td>
                  <td className="px-3 py-2">{outcome.entity_type}</td><td className="px-3 py-2">{outcome.qid ?? "-"}</td>
                  <td className="px-3 py-2">{outcome.status === "would_update" ? "Eligible to attempt update" : outcome.status}</td>
                  <td className="px-3 py-2">{outcome.message}</td>
                </tr>)}</tbody>
              </table>
            </CuratorTableScroll>}
          <OwnerPreviewLinks links={links} />
        </>}
      </div>}
    </section>
  );
}

export function OwnerPreviewLinks({links}: {links: LinkOutcome[]}) {
  return <>
    <h4 className="text-sm font-medium">Connection outcomes</h4>
    <p className="text-xs muted">These outcomes cover deferred connections only.</p>
    {links.length === 0 ? <p className="text-sm muted">No connection outcomes.</p> :
      <CuratorTableScroll className="max-h-[min(50vh,28rem)]">
        <table className="w-full text-sm" data-testid="wikidata-owner-preview-links">
          <thead className="text-xs uppercase muted"><tr>
            {["Source", "QID", "Property", "Target", "Status", "Reason"].map((label) => <th key={label} className="text-left px-3 py-2">{label}</th>)}
          </tr></thead>
          <tbody>{links.map((link, index) => <tr key={`${link.source_local_id}:${link.property_id}:${link.target_local_id}:${index}`} className="border-t border-white/5">
            <td className="px-3 py-2">{link.source_local_id}</td><td className="px-3 py-2">{link.qid ?? "-"}</td>
            <td className="px-3 py-2">{link.property_id}</td><td className="px-3 py-2">{link.target_local_id}</td>
            <td className="px-3 py-2">{link.status}</td><td className="px-3 py-2">{link.message}</td>
          </tr>)}</tbody>
        </table>
      </CuratorTableScroll>}
  </>;
}
