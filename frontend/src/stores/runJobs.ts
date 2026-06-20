import {create} from "zustand";

import {
  RunJobs,
  type RunJobSnapshot,
  type RunJobStatus,
} from "@/api/runJobs";

const ACTIVE: RunJobStatus[] = ["queued", "running"];
const POLL_MS = 2000;

interface RunJobsState {
  jobs: Record<string, RunJobSnapshot>;
  pollTimer: ReturnType<typeof setInterval> | null;
  refresh: () => Promise<void>;
  ensurePolling: () => void;
  stopPolling: () => void;
  cancelJob: (runId: string, jobId: string) => Promise<void>;
  jobForRun: (runId: string, kind: string) => RunJobSnapshot | null;
  activeJobs: () => RunJobSnapshot[];
}

export const useRunJobs = create<RunJobsState>((set, get) => ({
  jobs: {},
  pollTimer: null,

  async refresh() {
    try {
      const {jobs} = await RunJobs.listMine(true);
      const map: Record<string, RunJobSnapshot> = {};
      for (const j of jobs) map[j.id] = j;
      set({jobs: map});
      if (jobs.length === 0) {
        get().stopPolling();
      }
    } catch {
      // non-fatal
    }
  },

  ensurePolling() {
    const {pollTimer} = get();
    if (pollTimer != null) return;
    void get().refresh();
    const id = setInterval(() => { void get().refresh(); }, POLL_MS);
    set({pollTimer: id});
  },

  stopPolling() {
    const {pollTimer} = get();
    if (pollTimer != null) clearInterval(pollTimer);
    set({pollTimer: null});
  },

  async cancelJob(runId, jobId) {
    await RunJobs.cancel(runId, jobId);
    await get().refresh();
  },

  jobForRun(runId, kind) {
    return Object.values(get().jobs).find(
      (j) => j.run_id === runId && j.kind === kind && ACTIVE.includes(j.status),
    ) ?? null;
  },

  activeJobs() {
    return Object.values(get().jobs).filter((j) => ACTIVE.includes(j.status));
  },
}));

export function isJobActive(status: RunJobStatus): boolean {
  return ACTIVE.includes(status);
}
