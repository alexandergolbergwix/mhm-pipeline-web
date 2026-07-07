import {RunJobs, type RunJobKind, type RunJobSnapshot} from "@/api/runJobs";

export interface VerifySessionPayload {
  session_id?: string;
  run_id?: string;
  events?: unknown[];
  verdicts?: Array<Record<string, unknown>>;
}

/** Partial or final snapshot embedded in a verify job row (progress or result). */
export function jobVerifySessionSnapshot(
  job: RunJobSnapshot | null | undefined,
): VerifySessionPayload | null {
  const raw = job?.progress?.session_snapshot ?? job?.result?.session_snapshot;
  if (!raw || typeof raw !== "object") return null;
  const snap = raw as VerifySessionPayload;
  const verdictCount = (snap.verdicts ?? []).length;
  const eventCount = (snap.events ?? []).length;
  if (verdictCount === 0 && eventCount === 0) return null;
  return snap;
}

/** Session GET with Postgres job-row fallback (Heroku multi-dyno safe). */
export async function fetchVerifySessionWithJobFallback(
  runId: string,
  sessionId: string,
  jobKind: RunJobKind,
  fetchSession: (runId: string, sessionId: string) => Promise<VerifySessionPayload>,
  jobHint?: RunJobSnapshot | null,
): Promise<VerifySessionPayload> {
  const inline = jobVerifySessionSnapshot(jobHint);
  if (inline && (inline.verdicts ?? []).length > 0) {
    return {
      session_id: inline.session_id ?? sessionId,
      run_id: inline.run_id ?? runId,
      events: inline.events ?? [],
      verdicts: inline.verdicts ?? [],
    };
  }

  const full = await fetchSession(runId, sessionId);
  if ((full.verdicts ?? []).length > 0) {
    return full;
  }

  const {jobs} = await RunJobs.listForRun(runId, false);
  const job = jobs.find((j) =>
    j.kind === jobKind
    && String(j.params?.session_id ?? "") === sessionId
    && (j.status === "succeeded" || j.status === "running" || j.status === "queued"),
  );
  const snap = jobVerifySessionSnapshot(job);
  if (snap && (snap.verdicts ?? []).length > 0) {
    return {
      session_id: snap.session_id ?? sessionId,
      run_id: snap.run_id ?? runId,
      events: snap.events ?? [],
      verdicts: snap.verdicts ?? [],
    };
  }
  return full;
}
