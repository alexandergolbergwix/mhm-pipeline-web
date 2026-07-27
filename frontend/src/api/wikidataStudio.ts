import {api, csrfHeaders} from "@/api/client";
import type {AiVerdict} from "@/api/extractionApprovals";

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
  statement_count?: number;
  existing_qid?: string | null;
  entity_type?: string;
  local_id?: string;
  validation_issues?: ValidationIssue[];
  approved?: boolean | null;
  accept_foreign_modify?: boolean | null;
  accepted_foreign_qid?: string | null;
  upload_outcome?: string | null;
  upload_message?: string | null;
  upload_at?: string | null;
  ai_verdict?: AiVerdict | null;
  ai_verdict_at?: string | null;
  source_uri?: string | null;
  hmo_wikibase_id?: string | null;
  projection_source?: "hmo_wikibase" | "legacy" | string | null;
  source_fingerprint?: string | null;
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
  source: "legacy" | "canonical";
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

export type WikidataUploadTarget = "dry_run" | "test" | "live";

export interface UploadResponse {
  dry_run: boolean;
  upload_target?: WikidataUploadTarget;
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
  accept_foreign_modify?: boolean;
  accepted_foreign_qid?: string | null;
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
  approved?: boolean | null;
  accept_foreign_modify?: boolean | null;
  accepted_foreign_qid?: string | null;
}

export interface WikidataItemPushResult {
  local_id: string;
  status: string;
  qid: string | null;
  message: string;
  moratorium_lifted?: boolean;
  test_mode?: boolean;
}

export interface WikidataItemReconcileResult {
  local_id: string;
  status: string;
  qid: string | null;
  method?: string;
  message: string;
}

export interface ValidationErrorsResponse {
  run_id: string;
  count: number;
  items: StudioItem[];
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
  source?: "legacy" | "canonical";
  approvedOnly?: boolean;
  forceRebuild?: boolean;
  listView?: boolean;
  entityType?: string | null;
  q?: string | null;
  sort?: string;
  sortDir?: string;
  page?: number;
  pageSize?: number;
  uploadOutcome?: string | null;
}

/** Backend caps ``page_size`` at 500 on ``GET /wikidata-studio``. */
export const STUDIO_MAX_PAGE_SIZE = 500;

export async function fetchAllStudioItems(
  runId: string,
  params: Omit<StudioBuildParams, "page" | "pageSize"> = {},
): Promise<StudioBuild> {
  const pageSize = STUDIO_MAX_PAGE_SIZE;
  let page = 1;
  let merged: StudioBuild | null = null;
  const allItems: StudioItem[] = [];
  while (true) {
    const chunk = await Studio.build(runId, {...params, page, pageSize, listView: true});
    if (!merged) merged = chunk;
    allItems.push(...chunk.items);
    if (allItems.length >= chunk.total || chunk.items.length === 0) break;
    page += 1;
  }
  return {
    ...merged!,
    items: allItems,
    page: 1,
    page_size: allItems.length,
  };
}

export const Studio = {
  build: (runId: string, params: StudioBuildParams = {}) => {
    const {
      source = "canonical",
      approvedOnly = true,
      forceRebuild = false,
      listView = false,
      entityType,
      q,
      sort,
      sortDir,
      page,
      pageSize,
      uploadOutcome,
    } = params;
    const qs = new URLSearchParams();
    qs.set("source", source);
    qs.set("approved_only", approvedOnly ? "true" : "false");
    if (forceRebuild) qs.set("force_rebuild", "true");
    if (listView) qs.set("list_view", "true");
    if (entityType && entityType !== "all") qs.set("entity_type", entityType);
    if (q) qs.set("q", q);
    if (sort) qs.set("sort", sort);
    if (sortDir) qs.set("sort_dir", sortDir);
    if (page != null) qs.set("page", String(page));
    if (pageSize != null) qs.set("page_size", String(pageSize));
    if (uploadOutcome) qs.set("upload_outcome", uploadOutcome);
    return api.get<StudioBuild>(`/runs/${runId}/wikidata-studio?${qs.toString()}`);
  },

  fetchItem: (
    runId: string,
    localId: string,
    params: Pick<StudioBuildParams, "source" | "approvedOnly"> = {},
  ) => {
    const {source = "canonical", approvedOnly = true} = params;
    const qs = new URLSearchParams();
    qs.set("source", source);
    qs.set("approved_only", approvedOnly ? "true" : "false");
    return api.get<StudioItem>(
      `/runs/${runId}/wikidata-studio/items/${encodeURIComponent(localId)}?${qs.toString()}`,
    );
  },

  qsUrl: (runId: string, approvedOnly = true, uploadApprovedOnly = false, gated = true, source: "legacy" | "canonical" = "canonical") =>
    `/api/runs/${runId}/wikidata-studio/quickstatements.txt?source=${source}&approved_only=${approvedOnly ? "true" : "false"}${uploadApprovedOnly ? "&upload_approved_only=true" : ""}&gated=${gated ? "true" : "false"}`,

  reconcile: (runId: string, approvedOnly = true, source: "legacy" | "canonical" = "canonical") =>
    api.post<ReconcileResponse>(
      `/runs/${runId}/wikidata-studio/reconcile?source=${source}&approved_only=${approvedOnly ? "true" : "false"}`,
      {},
    ),

  upload: (runId: string, opts: {
    dry_run?: boolean;
    upload_target?: WikidataUploadTarget;
    approved_only: boolean;
    upload_approved_only?: boolean;
    source?: "legacy" | "canonical";
  }) => {
    const qs = new URLSearchParams();
    qs.set("approved_only", opts.approved_only ? "true" : "false");
    if (opts.upload_target) qs.set("upload_target", opts.upload_target);
    else if (opts.dry_run != null) qs.set("dry_run", opts.dry_run ? "true" : "false");
    if (opts.upload_approved_only) qs.set("upload_approved_only", "true");
    if (opts.source) qs.set("source", opts.source);
    return api.post<UploadResponse>(
      `/runs/${runId}/wikidata-studio/upload?${qs.toString()}`,
      {},
    );
  },

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

  pushItem(
    runId: string,
    localId: string,
    uploadTarget: "test" | "live" = "test",
  ): Promise<WikidataItemPushResult> {
    return api.post(
      `/runs/${runId}/wikidata-studio/items/${encodeURIComponent(localId)}/push?upload_target=${uploadTarget}`,
      {},
    );
  },

  reconcileItem(runId: string, localId: string): Promise<WikidataItemReconcileResult> {
    return api.post(
      `/runs/${runId}/wikidata-studio/items/${encodeURIComponent(localId)}/reconcile`,
      {},
    );
  },

  applyAiFixes(
    runId: string,
    localId: string,
    fixes: Array<Record<string, unknown>>,
  ): Promise<ItemOverrideResponse> {
    return api.post(
      `/runs/${runId}/wikidata-studio/items/${encodeURIComponent(localId)}/ai-fixes/apply`,
      {fixes},
    );
  },

  clearOverride(runId: string, localId: string): Promise<void> {
    return api.del(
      `/runs/${runId}/wikidata-studio/items/${encodeURIComponent(localId)}`,
    );
  },

  validationErrors(runId: string, onWikiOnly = false): Promise<ValidationErrorsResponse> {
    const qs = onWikiOnly ? "?on_wikidata_only=true" : "";
    return api.get(`/runs/${runId}/wikidata-studio/items/validation-errors${qs}`);
  },

  cachedVerdicts(runId: string, tierModel?: string): Promise<Record<string, AiVerdict>> {
    const qs = tierModel ? `?tier_model=${encodeURIComponent(tierModel)}` : "";
    return api.get(`/runs/${runId}/wikidata-studio/items/ai-verify/cached-verdicts${qs}`);
  },

  exportItemsUrl(runId: string, format: "json" | "csv"): string {
    return `/api/runs/${runId}/wikidata-studio/items/export?format=${format}`;
  },

  async importItems(runId: string, file: File): Promise<{imported: number; skipped: number}> {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`/api/runs/${runId}/wikidata-studio/items/import`, {
      method: "POST",
      credentials: "include",
      headers: csrfHeaders("POST"),
      body: form,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const payload = await res.json() as {detail?: string};
        if (payload.detail) detail = payload.detail;
      } catch { /* ignore */ }
      throw new Error(detail);
    }
    return res.json() as Promise<{imported: number; skipped: number}>;
  },
};
