import {useEffect, useState} from "react";

import {RunJobs, type RunJobSnapshot} from "@/api/runJobs";
import {isJobActive, useRunJobs} from "@/stores/runJobs";

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
  const storeJob = useRunJobs((s) =>
    runId ? s.jobForRun(runId, kind) : null,
  );

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    void RunJobs.listForRun(runId, true).then(({jobs}) => {
      if (cancelled) return;
      const active = jobs.find((j) => j.kind === kind);
      if (active) {
        setTrackedJobId(active.id);
        sync(active);
        ensureJobPolling();
      }
    });
    return () => { cancelled = true; };
  }, [runId, kind, ensureJobPolling, sync]);

  useEffect(() => {
    if (storeJob) sync(storeJob);
  }, [storeJob, sync]);

  useEffect(() => {
    if (!runId || !trackedJobId) return;
    const rid = runId;
    const jid = trackedJobId;
    let cancelled = false;
    async function poll() {
      try {
        const job = await RunJobs.get(rid, jid);
        if (cancelled) return;
        sync(job);
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
  }, [runId, trackedJobId, sync]);

  const activeJob = storeJob ?? null;
  return {activeJob, trackedJobId, setTrackedJobId, ensureJobPolling, cancelJob};
}
