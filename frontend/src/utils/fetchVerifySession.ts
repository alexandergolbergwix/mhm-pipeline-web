import {RunJobs, type RunJobKind} from "@/api/runJobs";

export interface VerifySessionPayload {
  session_id?: string;
  run_id?: string;
  events?: unknown[];
  verdicts?: Array<Record<string, unknown>>;
}

/** Session GET with Postgres job-row fallback (Heroku multi-dyno safe). */
export async function fetchVerifySessionWithJobFallback(
  runId: string,
  sessionId: string,
  jobKind: RunJobKind,
  fetchSession: (runId: string, sessionId: string) => Promise<VerifySessionPayload>,
): Promise<VerifySessionPayload> {
  const full = await fetchSession(runId, sessionId);
  if ((full.verdicts ?? []).length > 0) {
    return full;
  }

  const {jobs} = await RunJobs.listForRun(runId, false);
  const job = jobs.find((j) =>
    j.kind === jobKind
    && j.status === "succeeded"
    && String(j.params?.session_id ?? "") === sessionId,
  );
  const snap = job?.result?.session_snapshot;
  if (snap && typeof snap === "object") {
    const s = snap as VerifySessionPayload;
    if ((s.verdicts ?? []).length > 0) {
      return {
        session_id: s.session_id ?? sessionId,
        run_id: s.run_id ?? runId,
        events: s.events ?? [],
        verdicts: s.verdicts ?? [],
      };
    }
  }
  return full;
}
