import {useCallback, useEffect, useMemo, useRef} from "react";

import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import type {UploadOutcome, WikidataUploadTarget} from "@/api/wikidataStudio";
import {Glass, GlassPill} from "@/components/glass";
import {JobProgressInline} from "@/components/jobs/JobProgressInline";
import {isJobActive, useRunJobs} from "@/stores/runJobs";
import {
  liveUploadOutcomeRows,
  progressOutcomeCounts,
  tallyWikidataUploadOutcomeCounts,
  UploadOutcomeCountStrip,
  UploadOutcomeTable,
} from "@/utils/wikidataUploadOutcomes";

export interface WikidataUploadProgressModalProps {
  runId: string;
  jobId: string;
  onClose: () => void;
}

const KNOWN_TARGETS = new Set(["dry_run", "test", "live"]);

/** Resolve upload target without inventing ``live`` when evidence is missing. */
export function resolveUploadTarget(
  job: RunJobSnapshot | null | undefined,
  sticky?: WikidataUploadTarget | "dry_run" | null,
): WikidataUploadTarget | "dry_run" {
  const candidates = [
    job?.params?.upload_target,
    job?.progress?.upload_target,
    job?.result?.upload_target,
  ];
  for (const raw of candidates) {
    if (typeof raw === "string" && KNOWN_TARGETS.has(raw)) {
      return raw as WikidataUploadTarget;
    }
  }
  if (job?.params?.dry_run || job?.result?.dry_run) return "dry_run";
  if (job?.result?.test_mode || job?.params?.test_mode) return "test";
  if (sticky && KNOWN_TARGETS.has(sticky)) return sticky;
  // Safest default while the snapshot is loading — never imply live writes.
  return "dry_run";
}

const TARGET_BADGE: Record<string, string> = {
  dry_run: "Dry-run",
  test: "Test Wikidata",
  live: "Live Wikidata",
};

const TITLE_BY_TARGET: Record<string, string> = {
  dry_run: "Dry-run upload progress",
  test: "Test upload progress",
  live: "Live upload progress",
};

export function WikidataUploadProgressModal({
  runId,
  jobId,
  onClose,
}: WikidataUploadProgressModalProps) {
  const job = useRunJobs((s) => s.jobs[jobId] ?? null);
  const upsertJob = useRunJobs((s) => s.upsertJob);
  const cancelJob = useRunJobs((s) => s.cancelJob);
  const ensurePolling = useRunJobs((s) => s.ensurePolling);
  const stickyTargetRef = useRef<WikidataUploadTarget | "dry_run" | null>(null);

  useEffect(() => {
    ensurePolling();
    if (job?.run_id === runId && job.id === jobId) return;
    let cancelled = false;
    void RunJobs.get(runId, jobId).then((fetched) => {
      if (!cancelled) upsertJob(fetched);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [runId, jobId, job?.run_id, job?.id, upsertJob, ensurePolling]);

  const uploadTarget = resolveUploadTarget(job, stickyTargetRef.current);
  useEffect(() => {
    if (KNOWN_TARGETS.has(uploadTarget)) {
      stickyTargetRef.current = uploadTarget;
    }
  }, [uploadTarget]);

  const terminal = job != null && !isJobActive(job.status);
  const terminalOutcomes = (job?.result?.outcomes as UploadOutcome[] | undefined) ?? undefined;
  const rows = liveUploadOutcomeRows(
    job?.progress,
    terminalOutcomes,
    Boolean(terminal && terminalOutcomes?.length),
  );
  const counts = useMemo(() => {
    const fromProgress = progressOutcomeCounts(job?.progress);
    if (fromProgress) return fromProgress;
    if (terminalOutcomes?.length) return tallyWikidataUploadOutcomeCounts(terminalOutcomes);
    return tallyWikidataUploadOutcomeCounts([]);
  }, [job?.progress, terminalOutcomes]);

  const dryRun = uploadTarget === "dry_run";

  const handleCancel = useCallback(async () => {
    if (!job || !isJobActive(job.status)) return;
    const ok = window.confirm("Cancel this Wikidata upload job?");
    if (!ok) return;
    await cancelJob(runId, jobId);
  }, [job, cancelJob, runId, jobId]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/60 backdrop-blur-md p-4 md:p-6"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      data-testid="wikidata-upload-progress-modal"
    >
      <Glass variant="modal" className="max-w-5xl w-full max-h-full overflow-auto p-6 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="kicker">Wikidata upload</div>
            <h3 className="text-xl font-semibold flex flex-wrap items-center gap-2">
              {TITLE_BY_TARGET[uploadTarget] ?? "Upload progress"}
              <GlassPill
                className="px-2 py-0.5 text-[10px]"
                data-testid="wikidata-upload-progress-target-pill"
              >
                {TARGET_BADGE[uploadTarget] ?? uploadTarget}
              </GlassPill>
            </h3>
          </div>
          <button onClick={onClose} className="button-ghost !py-1 !px-3 text-sm" title="Close">
            X
          </button>
        </div>

        {job ? (
          <JobProgressInline
            job={job}
            labels={{
              running: "Uploading to Wikidata…",
              succeeded: "Upload complete:",
              failed: "Upload failed:",
              cancelled: "Upload cancelled:",
            }}
          />
        ) : (
          <p className="muted text-sm">Loading job…</p>
        )}

        <UploadOutcomeCountStrip counts={counts} dryRun={dryRun} />

        <UploadOutcomeTable rows={rows} />

        <div className="flex flex-wrap gap-2 justify-end pt-2 border-t border-white/5">
          {job && isJobActive(job.status) && (
            <button
              type="button"
              className="button-ghost text-sm text-danger"
              onClick={() => void handleCancel()}
              data-testid="wikidata-upload-modal-cancel"
            >
              Cancel upload
            </button>
          )}
          <button type="button" className="button-primary text-sm" onClick={onClose}>
            Close
          </button>
        </div>
      </Glass>
    </div>
  );
}
