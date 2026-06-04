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

export interface ValidationIssue {
  code: string;
  severity: "error" | "warning";
  message: string;
}

export interface StudioItem {
  labels?: Record<string, string>;
  descriptions?: Record<string, string>;
  aliases?: Record<string, string[]>;
  statements?: Snak[];
  existing_qid?: string | null;
  entity_type?: string;
  local_id?: string;
  validation_issues?: ValidationIssue[];
  approved?: boolean | null;
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

export interface ItemOverridePayload {
  labels?: Record<string, string | null>;
  descriptions?: Record<string, string | null>;
  aliases?: Record<string, string[] | null>;
  add_statements?: Array<Record<string, unknown>>;
  remove_statements?: number[];
  statement_edits?: Record<string, Record<string, unknown>>;
  approved?: boolean;
}

export interface ItemOverrideResponse {
  run_id: string;
  local_id: string;
  labels: Record<string, unknown>;
  descriptions: Record<string, unknown>;
  aliases: Record<string, unknown>;
  add_statements: Array<Record<string, unknown>>;
  remove_statements: number[];
  statement_edits: Record<string, unknown>;
}

export const Studio = {
  build: (runId: string, approvedOnly = true, forceRebuild = false) =>
    api.get<StudioBuild>(
      `/runs/${runId}/wikidata-studio?approved_only=${approvedOnly ? "true" : "false"}${forceRebuild ? "&force_rebuild=true" : ""}`,
    ),

  qsUrl: (runId: string, approvedOnly = true, uploadApprovedOnly = false) =>
    `/api/runs/${runId}/wikidata-studio/quickstatements.txt?approved_only=${approvedOnly ? "true" : "false"}${uploadApprovedOnly ? "&upload_approved_only=true" : ""}`,

  reconcile: (runId: string, approvedOnly = true) =>
    api.post<ReconcileResponse>(
      `/runs/${runId}/wikidata-studio/reconcile?approved_only=${approvedOnly ? "true" : "false"}`,
      {},
    ),

  upload: (runId: string, opts: { dry_run: boolean; approved_only: boolean; upload_approved_only?: boolean }) =>
    api.post<UploadResponse>(
      `/runs/${runId}/wikidata-studio/upload?dry_run=${opts.dry_run ? "true" : "false"}&approved_only=${opts.approved_only ? "true" : "false"}${opts.upload_approved_only ? "&upload_approved_only=true" : ""}`,
      {},
    ),

  patchItemOverride: (runId: string, localId: string, payload: ItemOverridePayload) =>
    api.patch<ItemOverrideResponse>(
      `/runs/${runId}/wikidata-studio/items/${encodeURIComponent(localId)}`,
      payload,
    ),
};
