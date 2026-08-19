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

// `refresh()` fires every POLL_MS on a bare setInterval with no backpressure —
// if one round-trip is slow (network jitter, a Heroku dyno hiccup) the next
// tick fires anyway, and two responses can land out of order. Applying
// whichever arrives last (instead of whichever was requested last) makes
// job progress visibly jump backward (e.g. 5803/7822 -> 1023/7822) before
// the next tick corrects it. A monotonic request counter discards any
// response that isn't for the most recently issued request.
let refreshSeq = 0;

export const useRunJobs = create<RunJobsState>((set, get) => ({
  jobs: {},
  pollTimer: null,

  async refresh() {
    const seq = ++refreshSeq;
    try {
      const {jobs} = await RunJobs.listMine(true);
      if (seq !== refreshSeq) return;
      // Merge active jobs into the existing map. listMine(active=true) omits
      // terminal jobs — replacing the map wholesale wiped cancelled/succeeded
      // snapshots and made open upload modals flicker (test badge ↔ live default).
      set((s) => {
        const map = {...s.jobs};
        for (const j of jobs) map[j.id] = j;
        return {jobs: map};
      });
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
