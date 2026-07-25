import {api} from "@/api/client";

export type RunJobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export type RunJobKind =
  | "extraction"
  | "ner_verify"
  | "wikidata_verify"
  | "rdf_build"
  | "wikidata_studio_build"
  | "wikidata_upload"
  | "hmo_schema_bootstrap"
  | "hmo_coverage"
  | "hmo_item_upload"
  | "hmo_item_verify"
  | "hmo_item_bulk_approve"
  | "hmo_item_build"
  | "hmo_manifest_build"
  | "hmo_manifest_upload"
  | "wikidata_item_bulk_approve";

export interface RunJobProgress {
  phase?: string;
  processed?: number;
  total?: number;
  message?: string;
  /** Optional counter unit for the tray/inline UI (e.g. ``steps``, ``items``). */
  unit?: string;
  current_entity?: string;
  current_control_number?: string;
  current_source?: string | null;
  matched?: boolean;
  is_new?: boolean;
  entity_kind?: string;
  session_id?: string;
  last_event_type?: string;
  mode?: string;
  /** Latest per-item upload outcome (HMO / Wikidata live upload). */
  item_outcome?: {
    local_id?: string;
    status?: string;
    wikibase_id?: string | null;
    qid?: string | null;
    message?: string | null;
    source_uri?: string;
  };
  /** Rolling window of recent item outcomes for mid-run table patches. */
  recent_item_outcomes?: Array<{
    local_id?: string;
    status?: string;
    wikibase_id?: string | null;
    qid?: string | null;
    message?: string | null;
    source_uri?: string;
  }>;
  session_snapshot?: {
    session_id?: string;
    run_id?: string;
    events?: unknown[];
    verdicts?: Array<Record<string, unknown>>;
  };
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
  extraction:               "AI Extraction",
  ner_verify:               "NER AI verify",
  wikidata_verify:          "Wikidata AI verify",
  rdf_build:                "RDF build",
  wikidata_studio_build:    "Wikidata Studio build",
  wikidata_upload:          "Wikidata upload",
  hmo_schema_bootstrap:     "HMO schema bootstrap",
  hmo_coverage:             "HMO coverage report",
  hmo_item_upload:          "HMO item upload",
  hmo_item_verify:          "HMO item AI verify",
  hmo_item_bulk_approve:    "HMO approve visible",
  hmo_item_build:           "HMO item build",
  hmo_manifest_build:       "HMO manifest build",
  hmo_manifest_upload:      "HMO manifest upload",
  wikidata_item_bulk_approve: "Wikidata approve visible",
};

export function jobRunHref(job: RunJobSnapshot): string {
  switch (job.kind) {
    case "extraction":
    case "ner_verify":
      return `/runs/${job.run_id}/extraction`;
    case "rdf_build":
      return `/runs/${job.run_id}/rdf`;
    case "wikidata_studio_build":
    case "wikidata_verify":
    case "wikidata_upload":
    case "wikidata_item_bulk_approve":
      return `/runs/${job.run_id}/wikidata-studio`;
    case "hmo_schema_bootstrap":
    case "hmo_coverage":
    case "hmo_item_upload":
    case "hmo_item_verify":
    case "hmo_item_bulk_approve":
    case "hmo_item_build":
    case "hmo_manifest_build":
    case "hmo_manifest_upload":
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
