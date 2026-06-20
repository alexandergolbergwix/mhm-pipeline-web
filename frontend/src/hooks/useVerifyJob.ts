import {useCallback, useEffect, useMemo, useRef, useState} from "react";

import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {isJobActive, useRunJobs} from "@/stores/runJobs";

interface UseVerifyJobOptions {
  runId: string;
  kind: "ner_verify" | "authority_verify" | "wikidata_verify";
  loadSession: (sessionId: string) => Promise<void>;
  onFailed?: (message: string) => void;
  onComplete?: () => void;
}

function jobFingerprint(job: RunJobSnapshot): string {
  return `${job.id}:${job.status}:${JSON.stringify(job.progress ?? {})}`;
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

  const storeJob = useMemo(() => {
    if (!runId) return null;
    return Object.values(jobsRecord).find(
      (j) => j.run_id === runId && j.kind === kind && isJobActive(j.status),
    ) ?? null;
  }, [jobsRecord, runId, kind]);

  const storeJobKey = storeJob ? jobFingerprint(storeJob) : null;

  const lastSessionRef = useRef<string | null>(null);
  const lastFingerprintRef = useRef<string | null>(null);
  const loadSessionRef = useRef(loadSession);
  const onCompleteRef = useRef(onComplete);
  const onFailedRef = useRef(onFailed);
  loadSessionRef.current = loadSession;
  onCompleteRef.current = onComplete;
  onFailedRef.current = onFailed;

  const applyJob = useCallback(async (job: RunJobSnapshot, force = false) => {
    const fp = jobFingerprint(job);
    if (!force && fp === lastFingerprintRef.current) return;
    lastFingerprintRef.current = fp;

    const sessionId = String(
      job.progress?.session_id ?? job.params?.session_id ?? "",
    );
    if (sessionId) {
      if (sessionId !== lastSessionRef.current) {
        lastSessionRef.current = sessionId;
      }
      await loadSessionRef.current(sessionId);
    }

    if (job.status === "queued" || job.status === "running") {
      setRunning(true);
      return;
    }
    setRunning(false);
    setJobId((current) => (current === job.id ? null : current));
    if (job.status === "succeeded") {
      onCompleteRef.current?.();
      return;
    }
    if (job.status === "failed") {
      onFailedRef.current?.(job.error ?? "Verification failed");
    }
  }, []);

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
    lastSessionRef.current = null;
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

  return {running, start, stop, jobId};
}
