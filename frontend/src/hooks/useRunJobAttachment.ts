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

  // Prefer the explicitly tracked job (including terminal succeeded/failed)
  // so WebSocket upserts and the global active-only poll still deliver a
  // final snapshot to sync — selectActiveJob alone drops completed jobs.
  const storeJob = useMemo(() => {
    if (!runId) return null;
    if (trackedJobId) {
      const tracked = jobsRecord[trackedJobId];
      if (tracked && tracked.run_id === runId && tracked.kind === kind) {
        return tracked;
      }
    }
    return selectActiveJob(jobsRecord, runId, kind);
  }, [jobsRecord, runId, kind, trackedJobId]);

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
        // Keep terminal snapshots in the store so sibling panels (e.g. the
        // items table) can react via the same jobs map before we drop the id.
        useRunJobs.getState().upsertJob(job);
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
