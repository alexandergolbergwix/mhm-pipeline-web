import { api } from "@/api/client";

export interface StudioSummary {
  total_items: number;
  manuscripts: number;
  persons: number;
  works: number;
  statements: number;
}

export interface Snak {
  property?: string;
  property_id?: string;
  property_label?: string;
  value?: unknown;
  value_id?: string;
  value_type?: string;
  value_label?: string;
  rank?: string;
  qualifiers?: Snak[];
  references?: Array<Snak | { snaks?: Snak[] }>;
  [k: string]: unknown;
}

export interface StudioItem {
  labels?: Record<string, string>;
  descriptions?: Record<string, string>;
  aliases?: Record<string, string[]>;
  statements?: Snak[];
  existing_qid?: string | null;
  entity_type?: string;
  local_id?: string;
}

export interface StudioBuild {
  items: StudioItem[];
  quickstatements: string;
  summary: StudioSummary;
  approved_match_count: number;
  pending_match_count: number;
  used_match_count: number;
  approved_only: boolean;
  record_count: number;
}

export interface ReconcileOutcome {
  local_id: string;
  label: string;
  entity_type: string;
  existing_qid: string | null;
  method: string;
  message: string;
}

export interface ReconcileResponse {
  reconciled: number;
  matched: number;
  outcomes: ReconcileOutcome[];
}

export interface UploadOutcome {
  local_id: string;
  label: string;
  entity_type: string;
  qid: string | null;
  status: "success" | "updated" | "exists" | "skipped" | "failed" | "pending" | string;
  message: string;
  added_properties: string[];
}

export interface UploadResponse {
  dry_run: boolean;
  moratorium_lifted: boolean;
  test_mode: boolean;
  outcomes: UploadOutcome[];
}

export const Studio = {
  build: (runId: string, approvedOnly = true) =>
    api.get<StudioBuild>(
      `/runs/${runId}/wikidata-studio?approved_only=${approvedOnly ? "true" : "false"}`,
    ),

  qsUrl: (runId: string, approvedOnly = true) =>
    `/api/runs/${runId}/wikidata-studio/quickstatements.txt?approved_only=${approvedOnly ? "true" : "false"}`,

  reconcile: (runId: string, approvedOnly = true) =>
    api.post<ReconcileResponse>(
      `/runs/${runId}/wikidata-studio/reconcile?approved_only=${approvedOnly ? "true" : "false"}`,
      {},
    ),

  upload: (runId: string, opts: { dry_run: boolean; approved_only: boolean }) =>
    api.post<UploadResponse>(
      `/runs/${runId}/wikidata-studio/upload?dry_run=${opts.dry_run ? "true" : "false"}&approved_only=${opts.approved_only ? "true" : "false"}`,
      {},
    ),
};
