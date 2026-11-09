import {useEffect, useMemo, useRef, useState} from "react";

import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {isJobActive, useRunJobs} from "@/stores/runJobs";
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
  options: {acceptJob?: (job: RunJobSnapshot) => boolean; autoAttach?: boolean; includeResult?: boolean} = {},
): UseRunJobAttachmentResult {
  const {acceptJob, autoAttach = true, includeResult = false} = options;
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
      if (tracked && tracked.run_id === runId && tracked.kind === kind && (!acceptJob || acceptJob(tracked))) {
        return tracked;
      }
    }
    if (!autoAttach) return null;
    return Object.values(jobsRecord).find((j) => j.run_id === runId && j.kind === kind
      && isJobActive(j.status) && (!acceptJob || acceptJob(j))) ?? null;
  }, [jobsRecord, runId, kind, trackedJobId, acceptJob, autoAttach]);

  const fingerprint = (job: RunJobSnapshot) => jobFingerprint(job) + (includeResult ? `:${JSON.stringify(job.result)}` : "");
  const storeJobKey = storeJob ? fingerprint(storeJob) : null;

  const applySync = (job: RunJobSnapshot, force = false) => {
    const fp = fingerprint(job);
    if (!force && fp === lastFingerprintRef.current) return;
    lastFingerprintRef.current = fp;
    syncRef.current(job);
  };

  useEffect(() => {
    if (!runId || !autoAttach) return;
    let cancelled = false;
    void RunJobs.listForRun(runId, true).then(({jobs}) => {
      if (cancelled) return;
      const active = jobs.find((j) => j.run_id === runId && j.kind === kind
        && isJobActive(j.status) && (!acceptJob || acceptJob(j)));
      if (active) {
        setTrackedJobId(active.id);
        applySync(active, true);
        ensureJobPolling();
      }
    }).catch((error: unknown) => console.warn("Could not load active jobs", error));
    return () => { cancelled = true; };
  }, [runId, kind, ensureJobPolling, autoAttach, acceptJob]);

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
        if (job.run_id !== rid || job.id !== jid || job.kind !== kind || (acceptJob && !acceptJob(job))) {
          setTrackedJobId(null);
          return;
        }
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
  }, [runId, trackedJobId, kind, acceptJob, includeResult]);

  const activeJob = storeJob ?? null;
  return {activeJob, trackedJobId, setTrackedJobId, ensureJobPolling, cancelJob};
}
