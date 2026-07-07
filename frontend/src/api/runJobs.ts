import {api} from "@/api/client";

export type RunJobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export type RunJobKind =
  | "authority_re_enrich"
  | "extraction"
  | "ner_verify"
  | "authority_verify"
  | "wikidata_verify"
  | "rdf_build"
  | "wikidata_studio_build"
  | "wikidata_upload"
  | "hmo_schema_bootstrap"
  | "hmo_coverage"
  | "hmo_item_upload"
  | "hmo_item_verify";

export interface RunJobProgress {
  phase?: string;
  processed?: number;
  total?: number;
  message?: string;
  current_entity?: string;
  current_control_number?: string;
  current_source?: string | null;
  matched?: boolean;
  is_new?: boolean;
  entity_kind?: string;
  session_id?: string;
  last_event_type?: string;
  mode?: string;
}

export interface RunJobSnapshot {
  id: string;
  project_id: string;
  run_id: string;
  kind: RunJobKind | string;
  status: RunJobStatus;
  progress: RunJobProgress;
  params: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  created_by: string | null;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export const JOB_KIND_LABELS: Record<string, string> = {
  authority_re_enrich:      "Authority re-enrich",
  extraction:               "AI Extraction",
  ner_verify:               "NER AI verify",
  authority_verify:         "Authority AI verify",
  wikidata_verify:          "Wikidata AI verify",
  rdf_build:                "RDF build",
  wikidata_studio_build:    "Wikidata Studio build",
  wikidata_upload:          "Wikidata upload",
  hmo_schema_bootstrap:     "HMO schema bootstrap",
  hmo_coverage:             "HMO coverage report",
  hmo_item_upload:          "HMO item upload",
  hmo_item_verify:          "HMO item AI verify",
};

export function jobRunHref(job: RunJobSnapshot): string {
  switch (job.kind) {
    case "authority_re_enrich":
    case "authority_verify":
      return `/runs/${job.run_id}`;
    case "extraction":
    case "ner_verify":
      return `/runs/${job.run_id}/extraction`;
    case "rdf_build":
      return `/runs/${job.run_id}/rdf`;
    case "wikidata_studio_build":
    case "wikidata_verify":
    case "wikidata_upload":
      return `/runs/${job.run_id}/wikidata-studio`;
    case "hmo_schema_bootstrap":
    case "hmo_coverage":
    case "hmo_item_upload":
    case "hmo_item_verify":
      return `/runs/${job.run_id}/hmo-studio`;
    default:
      return `/runs/${job.run_id}/overview`;
  }
}

export const RunJobs = {
  listMine: (active = false) =>
    api.get<{jobs: RunJobSnapshot[]}>(`/jobs/mine${active ? "?active=true" : ""}`),

  listForRun: (runId: string, active = false) =>
    api.get<{jobs: RunJobSnapshot[]}>(
      `/runs/${runId}/jobs${active ? "?active=true" : ""}`,
    ),

  get: (runId: string, jobId: string) =>
    api.get<RunJobSnapshot>(`/runs/${runId}/jobs/${jobId}`),

  start: (runId: string, kind: RunJobKind, params: Record<string, unknown> = {}) =>
    api.post<RunJobSnapshot>(`/runs/${runId}/jobs`, {kind, params}),

  cancel: (runId: string, jobId: string) =>
    api.post<RunJobSnapshot>(`/runs/${runId}/jobs/${jobId}/cancel`, {}),
};
