import {ApiError} from "@/api/client";
import {RunJobs, type RunJobKind, type RunJobSnapshot} from "@/api/runJobs";
import {isJobActive} from "@/stores/runJobs";

const DEFAULT_TIMEOUT_MS = 20 * 60_000;
const POLL_MS = 2_000;

/** Progress message when a job is queued behind the dyno concurrency gate. */
export function runJobQueuedMessage(job: RunJobSnapshot): string {
  const phase = job.progress?.phase;
  const msg = job.progress?.message;
  if (phase === "queued" && typeof msg === "string" && msg.trim()) {
    return msg;
  }
  if (typeof msg === "string" && msg.trim()) return msg;
  if (job.status === "queued") return "Queued — waiting for a worker…";
  return "";
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => { window.setTimeout(resolve, ms); });
}

export async function findActiveRunJob(
  runId: string,
  kind: string,
): Promise<RunJobSnapshot | null> {
  const {jobs} = await RunJobs.listForRun(runId, true);
  return jobs.find((j) => j.kind === kind) ?? null;
}

export async function ensureRunJob(
  runId: string,
  kind: RunJobKind,
  params: Record<string, unknown>,
): Promise<RunJobSnapshot> {
  try {
    return await RunJobs.start(runId, kind, params);
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      const active = await findActiveRunJob(runId, kind);
      if (active) return active;
    }
    throw e;
  }
}

export async function waitForRunJob(
  runId: string,
  jobId: string,
  opts?: {
    timeoutMs?: number;
    onUpdate?: (job: RunJobSnapshot) => void;
  },
): Promise<RunJobSnapshot> {
  const deadline = Date.now() + (opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  while (Date.now() < deadline) {
    const job = await RunJobs.get(runId, jobId);
    opts?.onUpdate?.(job);
    if (job.status === "succeeded") return job;
    if (job.status === "failed" || job.status === "cancelled") {
      throw new Error(job.error ?? `Job ${job.status}`);
    }
    await sleep(POLL_MS);
  }
  throw new Error("Background job timed out — try Cancel and start again.");
}

/** Parse ``job_id`` out of a 409 ``{code, message, job_id}`` response body. */
export function jobIdFromConflict(detail: string, expectedCode: string): string | null {
  try {
    const parsed = JSON.parse(detail) as {code?: string; job_id?: string};
    if (parsed.code === expectedCode && parsed.job_id) {
      return parsed.job_id;
    }
  } catch {
    /* detail was a plain string, not the structured conflict payload */
  }
  return null;
}

/** Parse ``job_id`` from a 409 ``studio_build_in_progress`` response body. */
export function studioBuildJobIdFromConflict(detail: string): string | null {
  return jobIdFromConflict(detail, "studio_build_in_progress");
}

function studioBuildProgressMessage(job: RunJobSnapshot): string {
  const queued = runJobQueuedMessage(job);
  if (queued) return queued;
  const msg = job.progress?.message;
  if (typeof msg === "string" && msg.trim()) return msg;
  if (job.status === "running") return "Building Wikidata items in the background…";
  return "Finishing build…";
}

/** Wait for an in-flight job, or start one when the API returns 409. */
export async function waitForStudioBuild(
  runId: string,
  params: {approvedOnly: boolean; forceRebuild: boolean; source?: "legacy" | "canonical"},
): Promise<void> {
  const active = await findActiveRunJob(runId, "wikidata_studio_build");
  if (active && isJobActive(active.status)) {
    await waitForRunJob(runId, active.id);
    return;
  }
  if (params.forceRebuild) {
    const job = await ensureRunJob(runId, "wikidata_studio_build", {
      approved_only: params.approvedOnly,
      force_rebuild: true,
      source: params.source || "legacy",
    });
    await waitForRunJob(runId, job.id);
  }
}

export async function loadStudioBuild(
  runId: string,
  fetchBuild: () => Promise<unknown>,
  opts?: {onProgress?: (message: string) => void},
): Promise<unknown> {
  try {
    return await fetchBuild();
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 409) throw e;
    const jobId =
      studioBuildJobIdFromConflict(e.detail)
      ?? (await findActiveRunJob(runId, "wikidata_studio_build"))?.id;
    if (!jobId) throw e;
    opts?.onProgress?.("Building Wikidata items in the background…");
    await waitForRunJob(runId, jobId, {
      onUpdate: (job) => { opts?.onProgress?.(studioBuildProgressMessage(job)); },
    });
    return fetchBuild();
  }
}

/**
 * Generic "GET a cached report, and on a 409 {code, job_id} conflict wait
 * for the background job that's (re)building it before re-fetching" flow.
 * Any endpoint following the wikidata-studio-build 409 convention (job
 * enqueued server-side, poll until terminal, then re-GET) can use this
 * instead of a bespoke retry loop that would otherwise hammer the slow
 * endpoint every time it fails.
 */
export async function loadWithJobFallback<T>(
  runId: string,
  jobKind: RunJobKind,
  conflictCode: string,
  fetchReport: () => Promise<T>,
  opts?: {onProgress?: (message: string) => void},
): Promise<T> {
  try {
    return await fetchReport();
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 409) throw e;
    const jobId =
      jobIdFromConflict(e.detail, conflictCode)
      ?? (await findActiveRunJob(runId, jobKind))?.id;
    if (!jobId) throw e;
    opts?.onProgress?.("Building in the background…");
    await waitForRunJob(runId, jobId, {
      onUpdate: (job) => {
        const queued = runJobQueuedMessage(job);
        const msg = job.progress?.message;
        opts?.onProgress?.(
          queued || (typeof msg === "string" && msg.trim() ? msg : "Building in the background…"),
        );
      },
    });
    return fetchReport();
  }
}

/** HMO Studio coverage report — mirrors {@link loadStudioBuild}'s 409 flow. */
export async function loadHmoCoverage<T>(
  runId: string,
  fetchCoverage: () => Promise<T>,
  opts?: {onProgress?: (message: string) => void},
): Promise<T> {
  return loadWithJobFallback(runId, "hmo_coverage", "hmo_coverage_in_progress", fetchCoverage, opts);
}
