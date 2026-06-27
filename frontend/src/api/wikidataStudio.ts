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

export interface PropertyInfo {
  id: string;
  label: string;
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
  // Server-side slicing
  total: number;
  page: number;
  page_size: number;
  // Precomputed aggregates
  approved_item_count: number;
  properties: PropertyInfo[];
  property_labels: Record<string, string>;
  cache_stale?: boolean;
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

export type CompareStatus = "same" | "conflict" | "wikidata_only" | "studio_only";

export interface CompareFieldRow {
  kind: "label" | "description" | "statement";
  key: string;
  label: string;
  wikidata_value: string | null;
  studio_value: string | null;
  status: CompareStatus;
  studio_statement_index: number | null;
}

export interface WikidataCompareResult {
  qid: string;
  studio_local_id: string;
  wikidata: {
    qid: string;
    labels: Record<string, string>;
    descriptions: Record<string, string>;
    statement_count: number;
  };
  studio_label: string;
  rows: CompareFieldRow[];
  has_conflicts: boolean;
  conflict_count: number;
}

export interface StudioBuildParams {
  approvedOnly?: boolean;
  forceRebuild?: boolean;
  entityType?: string | null;
  q?: string | null;
  sort?: string;
  sortDir?: string;
  page?: number;
  pageSize?: number;
}

export const Studio = {
  build: (runId: string, params: StudioBuildParams = {}) => {
    const {
      approvedOnly = true,
      forceRebuild = false,
      entityType,
      q,
      sort,
      sortDir,
      page,
      pageSize,
    } = params;
    const qs = new URLSearchParams();
    qs.set("approved_only", approvedOnly ? "true" : "false");
    if (forceRebuild) qs.set("force_rebuild", "true");
    if (entityType && entityType !== "all") qs.set("entity_type", entityType);
    if (q) qs.set("q", q);
    if (sort) qs.set("sort", sort);
    if (sortDir) qs.set("sort_dir", sortDir);
    if (page != null) qs.set("page", String(page));
    if (pageSize != null) qs.set("page_size", String(pageSize));
    return api.get<StudioBuild>(`/runs/${runId}/wikidata-studio?${qs.toString()}`);
  },

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

  compareWithWikidata: (
    runId: string,
    localId: string,
    opts: {qid?: string; approvedOnly?: boolean},
  ) => {
    const qs = new URLSearchParams();
    if (opts.qid) qs.set("qid", opts.qid);
    qs.set("approved_only", opts.approvedOnly !== false ? "true" : "false");
    const query = qs.toString();
    return api.get<WikidataCompareResult>(
      `/runs/${runId}/wikidata-studio/items/${encodeURIComponent(localId)}/wikidata-compare${query ? `?${query}` : ""}`,
    );
  },

  applyWikidataCompare: (
    runId: string,
    localId: string,
    body: {
      policy: "wikidata" | "studio" | "custom";
      choices: Array<{kind: string; key: string; source: "wikidata" | "studio"}>;
      qid?: string;
      approvedOnly?: boolean;
    },
  ) =>
    api.post<ItemOverrideResponse>(
      `/runs/${runId}/wikidata-studio/items/${encodeURIComponent(localId)}/wikidata-compare/apply`,
      body,
    ),
};
