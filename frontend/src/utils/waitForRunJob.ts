import {ApiError} from "@/api/client";
import {RunJobs, type RunJobKind, type RunJobSnapshot} from "@/api/runJobs";
import {isJobActive} from "@/stores/runJobs";

const DEFAULT_TIMEOUT_MS = 20 * 60_000;
const POLL_MS = 2_000;

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

/** Wait for an in-flight job, or start one when the API returns 409. */
export async function waitForStudioBuild(
  runId: string,
  params: {approvedOnly: boolean; forceRebuild: boolean},
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
    });
    await waitForRunJob(runId, job.id);
  }
}

export async function loadStudioBuild(
  runId: string,
  fetchBuild: () => Promise<unknown>,
): Promise<unknown> {
  try {
    return await fetchBuild();
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 409) throw e;
    const active = await findActiveRunJob(runId, "wikidata_studio_build");
    if (!active) throw e;
    await waitForRunJob(runId, active.id);
    return fetchBuild();
  }
}
