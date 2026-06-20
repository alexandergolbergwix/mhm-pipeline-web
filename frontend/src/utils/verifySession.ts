import type {RunJobSnapshot} from "@/api/runJobs";

/** True when the verify job's on-disk session trace is expected to exist. */
export function shouldLoadVerifySession(job: RunJobSnapshot): boolean {
  const sessionId = String(
    job.progress?.session_id ?? job.params?.session_id ?? "",
  );
  if (!sessionId) return false;
  if (
    job.status === "succeeded"
    || job.status === "failed"
    || job.status === "cancelled"
  ) {
    return true;
  }
  if (job.status === "queued" || job.status === "running") {
    return Number(job.progress?.processed ?? 0) > 0;
  }
  return false;
}
