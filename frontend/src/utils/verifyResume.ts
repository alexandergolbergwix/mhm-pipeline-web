import type {RunJobSnapshot} from "@/api/runJobs";

export interface VerifyResumeOffer {
  judged: number;
  total: number;
  remaining: number;
  sessionId: string | null;
  jobId: string;
  /** Prior job params (without secrets) for Continue. */
  params: Record<string, unknown>;
}

export function resumeOfferFromJob(job: RunJobSnapshot | null | undefined): VerifyResumeOffer | null {
  if (!job || job.status === "queued" || job.status === "running") return null;
  const result = job.result ?? {};
  const judged = Number(result.judged ?? job.progress?.processed ?? 0);
  const total = Number(result.total ?? job.progress?.total ?? 0);
  const explicit = result.resumable === true;
  const partial =
    judged > 0
    && (
      result.outcome === "partial"
      || result.interrupted === true
      || (total > 0 && judged < total)
      || job.status === "failed"
      || job.status === "cancelled"
    );
  if (!explicit && !(partial && (total === 0 || judged < total))) return null;
  if (total > 0 && judged >= total && result.outcome !== "partial") return null;
  return {
    judged,
    total: total || judged,
    remaining: total > judged ? total - judged : 0,
    sessionId: String(result.session_id ?? job.progress?.session_id ?? job.params?.session_id ?? "") || null,
    jobId: job.id,
    params: {...(job.params ?? {})},
  };
}

export function continueVerifyLabel(offer: VerifyResumeOffer): string {
  if (offer.total > 0) {
    return `Continue verification (${offer.judged}/${offer.total} done)`;
  }
  return `Continue verification (${offer.judged} judged)`;
}
