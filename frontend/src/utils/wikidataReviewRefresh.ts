import type {RunJobSnapshot} from "@/api/runJobs";

type ReviewJob = Pick<RunJobSnapshot, "id" | "run_id" | "kind" | "status" | "params">;

export class WikidataReviewRefreshTracker {
  private initialized = false;
  private readonly seen = new Set<string>();
  private readonly runId: string;
  private readonly source: string;

  constructor(runId: string, source: string) {
    this.runId = runId;
    this.source = source;
  }

  observe(jobs: Iterable<ReviewJob>): boolean {
    let changed = false;
    for (const job of jobs) {
      if (job.run_id !== this.runId) continue;
      if (!["succeeded", "failed", "cancelled"].includes(job.status)) continue;
      if (job.params.source && job.params.source !== this.source) continue;
      const liveUpload = job.kind === "wikidata_upload" &&
        job.params.upload_target === "live";
      if (!liveUpload && job.kind !== "wikidata_verify") continue;
      if (this.seen.has(job.id)) continue;
      this.seen.add(job.id);
      changed = true;
    }
    const refresh = this.initialized && changed;
    this.initialized = true;
    return refresh;
  }
}
