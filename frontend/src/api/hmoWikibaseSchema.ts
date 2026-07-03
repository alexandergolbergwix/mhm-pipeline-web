import { api } from "@/api/client";
import type { RunJobSnapshot } from "@/api/runJobs";

export interface HmoSchemaStatus {
  total_classes: number;
  total_properties: number;
  mapped_classes: number;
  mapped_properties: number;
  missing_sample: string[];
  bot_username_set: boolean;
  bot_password_set: boolean;
}

export interface HmoSchemaBootstrapEntry {
  ontology_uri: string;
  entity_kind: string;
  label: string;
  wikibase_id: string | null;
  status: "created" | "skipped" | "would_create" | "failed" | string;
  message: string;
}

export interface HmoSchemaBootstrapResult {
  dry_run: boolean;
  created: number;
  skipped: number;
  failed: number;
  would_create: number;
  entries: HmoSchemaBootstrapEntry[];
}

export const HmoWikibaseSchema = {
  status: () => api.get<HmoSchemaStatus>("/hmo-wikibase-schema/status"),

  /**
   * Dry-run stays synchronous (returns the result directly). A live
   * bootstrap (`dryRun=false`) makes ~380 sequential external calls — too
   * slow for one HTTP request — so the backend spawns a `run_jobs`
   * background job and returns its snapshot immediately; the caller must
   * track `job.id` (e.g. via `useRunJobAttachment`) for progress.
   */
  bootstrap: (dryRun: boolean, runId?: string) =>
    api.post<HmoSchemaBootstrapResult | RunJobSnapshot>(
      "/hmo-wikibase-schema/bootstrap",
      { dry_run: dryRun, run_id: dryRun ? undefined : runId },
    ),
};

export function isSchemaBootstrapJob(
  r: HmoSchemaBootstrapResult | RunJobSnapshot,
): r is RunJobSnapshot {
  return "kind" in r && r.kind === "hmo_schema_bootstrap";
}
