import {useEffect, useMemo, useRef, useState} from "react";

import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {isJobActive, selectActiveJob, useRunJobs} from "@/stores/runJobs";
import {jobFingerprint, useLatestRef} from "@/utils/renderStable";

interface UseRunJobAttachmentResult {
  activeJob: RunJobSnapshot | null;
  trackedJobId: string | null;
  setTrackedJobId: (id: string | null) => void;
  ensureJobPolling: () => void;
  cancelJob: (runId: string, jobId: string) => Promise<void>;
}

export function useRunJobAttachment(
  runId: string | undefined,
  kind: string,
  sync: (job: RunJobSnapshot) => void,
): UseRunJobAttachmentResult {
  const [trackedJobId, setTrackedJobId] = useState<string | null>(null);
  const ensureJobPolling = useRunJobs((s) => s.ensurePolling);
  const cancelJob = useRunJobs((s) => s.cancelJob);
  const jobsRecord = useRunJobs((s) => s.jobs);
  const syncRef = useLatestRef(sync);
  const lastFingerprintRef = useRef<string | null>(null);

  const storeJob = useMemo(
    () => (runId ? selectActiveJob(jobsRecord, runId, kind) : null),
    [jobsRecord, runId, kind],
  );

  const storeJobKey = storeJob ? jobFingerprint(storeJob) : null;

  const applySync = (job: RunJobSnapshot, force = false) => {
    const fp = jobFingerprint(job);
    if (!force && fp === lastFingerprintRef.current) return;
    lastFingerprintRef.current = fp;
    syncRef.current(job);
  };

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    void RunJobs.listForRun(runId, true).then(({jobs}) => {
      if (cancelled) return;
      const active = jobs.find((j) => j.kind === kind);
      if (active) {
        setTrackedJobId(active.id);
        applySync(active, true);
        ensureJobPolling();
      }
    });
    return () => { cancelled = true; };
  }, [runId, kind, ensureJobPolling]);

  useEffect(() => {
    if (!storeJob || !storeJobKey) return;
    applySync(storeJob);
  }, [storeJobKey, storeJob]);

  useEffect(() => {
    if (!runId || !trackedJobId) return;
    const rid = runId;
    const jid = trackedJobId;
    let cancelled = false;
    async function poll() {
      try {
        const job = await RunJobs.get(rid, jid);
        if (cancelled) return;
        applySync(job);
        if (!isJobActive(job.status)) {
          setTrackedJobId(null);
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
  }, [runId, trackedJobId]);

  const activeJob = storeJob ?? null;
  return {activeJob, trackedJobId, setTrackedJobId, ensureJobPolling, cancelJob};
}
