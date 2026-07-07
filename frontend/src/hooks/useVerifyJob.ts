import {useCallback, useEffect, useMemo, useRef, useState} from "react";

import {ApiError} from "@/api/client";
import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {isJobActive, selectActiveJob, useRunJobs} from "@/stores/runJobs";
import {jobVerifySessionSnapshot} from "@/utils/fetchVerifySession";
import {useLatestRef} from "@/utils/renderStable";
import {shouldLoadVerifySession} from "@/utils/verifySession";

interface UseVerifyJobOptions {
  runId: string;
  kind: "ner_verify" | "authority_verify" | "wikidata_verify" | "hmo_item_verify";
  loadSession: (sessionId: string, job?: RunJobSnapshot) => Promise<void>;
  onFailed?: (message: string) => void;
  onComplete?: () => void;
}

/** Poll key that ignores the growing session_snapshot blob in progress. */
function verifyJobPollKey(job: RunJobSnapshot): string {
  const p = job.progress ?? {};
  const snap = jobVerifySessionSnapshot(job);
  const verdictCount = (snap?.verdicts ?? []).length;
  return [
    job.id,
    job.status,
    p.processed ?? 0,
    p.total ?? 0,
    verdictCount,
    p.last_event_type ?? "",
    p.phase ?? "",
  ].join(":");
}

export function useVerifyJob({
  runId,
  kind,
  loadSession,
  onFailed,
  onComplete,
}: UseVerifyJobOptions) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const ensurePolling = useRunJobs((s) => s.ensurePolling);
  const cancelJob = useRunJobs((s) => s.cancelJob);
  const jobsRecord = useRunJobs((s) => s.jobs);

  const storeJob = useMemo(
    () => (runId ? selectActiveJob(jobsRecord, runId, kind) : null),
    [jobsRecord, runId, kind],
  );

  const storeJobKey = storeJob ? verifyJobPollKey(storeJob) : null;

  const lastFingerprintRef = useRef<string | null>(null);
  const loadSessionRef = useLatestRef(loadSession);
  const onCompleteRef = useLatestRef(onComplete);
  const onFailedRef = useLatestRef(onFailed);

  const applyJob = useCallback(async (job: RunJobSnapshot, force = false) => {
    const fp = verifyJobPollKey(job);
    if (!force && fp === lastFingerprintRef.current) return;
    lastFingerprintRef.current = fp;

    const sessionId = String(
      job.progress?.session_id ?? job.params?.session_id ?? "",
    );
    const hasInlineSnapshot = Boolean(jobVerifySessionSnapshot(job)?.verdicts?.length);
    if (sessionId && (shouldLoadVerifySession(job) || hasInlineSnapshot)) {
      try {
        await loadSessionRef.current(sessionId, job);
      } catch (e) {
        const isActive = job.status === "queued" || job.status === "running";
        if (e instanceof ApiError && e.status === 404 && isActive) {
          // Worker has not written trace.jsonl yet — normal at job start.
        } else if (!isActive) {
          onFailedRef.current?.(
            e instanceof Error ? e.message : "session not found",
          );
        }
      }
    }

    if (job.status === "queued" || job.status === "running") {
      setRunning(true);
      return;
    }
    setRunning(false);
    setJobId((current) => (current === job.id ? null : current));
    if (job.status === "succeeded") {
      const result = job.result ?? {};
      const judged = Number(result.judged ?? job.progress?.processed ?? 0);
      const scopeTotal = Number(result.total ?? job.progress?.total ?? 0);
      const skipped = Number(result.uncached_skipped ?? 0);
      const outcome = String(result.outcome ?? "");
      if (judged === 0) {
        const msg = skipped > 0
          ? `No entities were verified. ${skipped} were skipped because the eval-agent could not run on this server.`
          : "Verification finished with no verdicts — check the eval-agent logs or retry.";
        onFailedRef.current?.(msg);
      } else if (
        skipped > 0
        || outcome === "partial"
        || (scopeTotal > 0 && judged < scopeTotal)
      ) {
        const partialHint = kind === "ner_verify"
          ? "some entities may have been below the confidence threshold or errored"
          : "some candidates could not be judged (eval-agent error)";
        const msg = skipped > 0
          ? `Verified ${judged} of ${scopeTotal || judged}. ${skipped} were skipped because the eval-agent could not run on this server.`
          : `Verified ${judged} of ${scopeTotal} — ${partialHint}.`;
        onFailedRef.current?.(msg);
      }
      onCompleteRef.current?.();
      return;
    }
    if (job.status === "failed") {
      onFailedRef.current?.(job.error ?? "Verification failed");
    }
  }, [loadSessionRef, onCompleteRef, onFailedRef]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    void RunJobs.listForRun(runId, true).then(({jobs}) => {
      if (cancelled) return;
      const active = jobs.find((j) => j.kind === kind && isJobActive(j.status));
      if (active) {
        setJobId(active.id);
        void applyJob(active, true);
        ensurePolling();
      }
    });
    return () => { cancelled = true; };
  }, [runId, kind, applyJob, ensurePolling]);

  useEffect(() => {
    if (!storeJob || !storeJobKey) return;
    void applyJob(storeJob);
  }, [storeJobKey, storeJob, applyJob]);

  useEffect(() => {
    if (!runId || !jobId) return;
    const rid = runId;
    const jid = jobId;
    let cancelled = false;
    async function poll() {
      try {
        const job = await RunJobs.get(rid, jid);
        if (cancelled) return;
        await applyJob(job);
        if (!isJobActive(job.status)) {
          setJobId(null);
        }
      } catch {
        // transient
      }
    }
    void poll();
    const id = window.setInterval(() => { void poll(); }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [runId, jobId, applyJob]);

  async function start(params: Record<string, unknown>) {
    setRunning(true);
    lastFingerprintRef.current = null;
    const job = await RunJobs.start(runId, kind, params);
    setJobId(job.id);
    await applyJob(job, true);
    ensurePolling();
    return job;
  }

  function stop() {
    if (jobId) void cancelJob(runId, jobId);
    setRunning(false);
  }

  return {running, start, stop, jobId, progress: storeJob?.progress ?? null};
}
