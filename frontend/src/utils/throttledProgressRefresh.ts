/**
 * Throttle review-table reloads while a long `run_jobs` worker is still
 * running. Live writes / approvals / verdicts land in Postgres incrementally;
 * without this, curator tables stay stale until the job finishes.
 */

export type ThrottledProgressRefreshOptions = {
  /** Minimum wall time between refreshes (default 3000 ms). */
  minMs?: number;
  /** Minimum increase in ``processed`` between refreshes (default 5). */
  minDelta?: number;
};

export type ThrottledProgressRefresh = {
  /** True when the caller should reload the table for this processed count. */
  shouldRefresh: (processed: number) => boolean;
  reset: () => void;
};

export function createThrottledProgressRefresh(
  opts?: ThrottledProgressRefreshOptions,
): ThrottledProgressRefresh {
  const minMs = opts?.minMs ?? 3000;
  const minDelta = opts?.minDelta ?? 5;
  let lastAt = 0;
  let lastProcessed = -1;

  return {
    shouldRefresh(processed: number) {
      if (!(processed > 0)) return false;
      const now = Date.now();
      const since = now - lastAt;
      const delta = processed - lastProcessed;
      if (lastProcessed < 0 || since >= minMs || delta >= minDelta) {
        lastAt = now;
        lastProcessed = processed;
        return true;
      }
      return false;
    },
    reset() {
      lastAt = 0;
      lastProcessed = -1;
    },
  };
}

/** Read ``progress.processed`` from a job-like snapshot (or 0). */
export function jobProcessedCount(
  job: {progress?: {processed?: unknown} | null} | null | undefined,
): number {
  return Number(job?.progress?.processed ?? 0);
}
