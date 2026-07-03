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
  /** Merge one job snapshot in immediately (e.g. from a WebSocket push),
   *  without waiting for the next 2s poll tick. The next `refresh()` still
   *  wins — this is a latency improvement, not a new source of truth. */
  upsertJob: (job: RunJobSnapshot) => void;
  /** Imperative getter only — do not use inside `useRunJobs(selector)`. */
  jobForRun: (runId: string, kind: string) => RunJobSnapshot | null;
  /** Imperative getter only — do not use inside `useRunJobs(selector)`. */
  activeJobs: () => RunJobSnapshot[];
}

export function isJobActive(status: RunJobStatus): boolean {
  return ACTIVE.includes(status);
}

export function selectActiveJob(
  jobs: Record<string, RunJobSnapshot>,
  runId: string,
  kind: string,
): RunJobSnapshot | null {
  return Object.values(jobs).find(
    (j) => j.run_id === runId && j.kind === kind && isJobActive(j.status),
  ) ?? null;
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

  upsertJob(job) {
    set((s) => ({jobs: {...s.jobs, [job.id]: job}}));
  },

  jobForRun(runId, kind) {
    return selectActiveJob(get().jobs, runId, kind);
  },

  activeJobs() {
    return Object.values(get().jobs).filter((j) => ACTIVE.includes(j.status));
  },
}));
