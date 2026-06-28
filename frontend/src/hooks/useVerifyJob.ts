import {useCallback, useEffect, useMemo, useRef, useState} from "react";

import {ApiError} from "@/api/client";
import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {isJobActive, selectActiveJob, useRunJobs} from "@/stores/runJobs";
import {jobFingerprint, useLatestRef} from "@/utils/renderStable";
import {shouldLoadVerifySession} from "@/utils/verifySession";

interface UseVerifyJobOptions {
  runId: string;
  kind: "ner_verify" | "authority_verify" | "wikidata_verify";
  loadSession: (sessionId: string) => Promise<void>;
  onFailed?: (message: string) => void;
  onComplete?: () => void;
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

  const storeJobKey = storeJob ? jobFingerprint(storeJob) : null;

  const lastFingerprintRef = useRef<string | null>(null);
  const loadSessionRef = useLatestRef(loadSession);
  const onCompleteRef = useLatestRef(onComplete);
  const onFailedRef = useLatestRef(onFailed);

  const applyJob = useCallback(async (job: RunJobSnapshot, force = false) => {
    const fp = jobFingerprint(job);
    if (!force && fp === lastFingerprintRef.current) return;
    lastFingerprintRef.current = fp;

    const sessionId = String(
      job.progress?.session_id ?? job.params?.session_id ?? "",
    );
    if (sessionId && shouldLoadVerifySession(job)) {
      try {
        await loadSessionRef.current(sessionId);
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
        const msg = skipped > 0
          ? `Verified ${judged} of ${scopeTotal || judged}. ${skipped} were skipped because the eval-agent could not run on this server.`
          : `Verified ${judged} of ${scopeTotal} — some candidates may have been below the confidence threshold or errored.`;
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
