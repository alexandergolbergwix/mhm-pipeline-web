import {useCallback, useEffect, useRef, useState} from "react";

import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {isJobActive, useRunJobs} from "@/stores/runJobs";

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
  const storeJob = useRunJobs((s) => (runId ? s.jobForRun(runId, kind) : null));
  const lastSessionRef = useRef<string | null>(null);

  const applyJob = useCallback(async (job: RunJobSnapshot) => {
    const sessionId = String(
      job.progress?.session_id ?? job.params?.session_id ?? "",
    );
    if (sessionId && sessionId !== lastSessionRef.current) {
      lastSessionRef.current = sessionId;
      await loadSession(sessionId);
    } else if (sessionId) {
      await loadSession(sessionId);
    }

    if (job.status === "queued" || job.status === "running") {
      setRunning(true);
      return;
    }
    setRunning(false);
    setJobId(null);
    if (job.status === "succeeded") {
      onComplete?.();
      return;
    }
    if (job.status === "failed") {
      onFailed?.(job.error ?? "Verification failed");
    }
  }, [loadSession, onComplete, onFailed]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    void RunJobs.listForRun(runId, true).then(({jobs}) => {
      if (cancelled) return;
      const active = jobs.find((j) => j.kind === kind);
      if (active) {
        setJobId(active.id);
        void applyJob(active);
        ensurePolling();
      }
    });
    return () => { cancelled = true; };
  }, [runId, kind, applyJob, ensurePolling]);

  useEffect(() => {
    if (storeJob) void applyJob(storeJob);
  }, [storeJob, applyJob]);

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
    const job = await RunJobs.start(runId, kind, params);
    setJobId(job.id);
    await applyJob(job);
    ensurePolling();
    return job;
  }

  function stop() {
    if (jobId) void cancelJob(runId, jobId);
    setRunning(false);
  }

  return {running, start, stop, jobId};
}
