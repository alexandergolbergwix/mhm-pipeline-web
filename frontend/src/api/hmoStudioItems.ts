import {api} from "@/api/client";
import type {AiVerdict} from "@/api/extractionApprovals";
import type {RuleVerdictSummary} from "@/api/ruleVerify";

export interface HmoResolvedClaim {
  property_id: string;
  datatype: string;
  value: unknown;
}

export interface HmoStudioItem {
  local_id: string;
  labels: Record<string, string>;
  descriptions: Record<string, string>;
  aliases?: Record<string, string[]>;
  class_qid: string;
  source_uri: string;
  claims: HmoResolvedClaim[];
  deferred_links?: Array<{source_local_id: string; property_id: string; target_local_id: string}>;
  skipped_statements?: string[];
  authority_evidence?: Array<{
    source: string;
    identifier: string;
    kind: string;
    accepted: boolean;
    reason?: string;
    details?: Record<string, string>;
  }>;
  status: string;
  wikibase_id: string | null;
  approved: boolean | null;
  shacl_issues: Array<{severity: string; message: string; focus_node?: string}>;
  has_blocking_shacl?: boolean;
  ai_verdict: AiVerdict | null;
  ai_verdict_at?: string | null;
  rule_verdict?: RuleVerdictSummary | null;
  rule_verdict_at?: string | null;
  override_present: boolean;
  override_id?: string | null;
  /** Latest wikibase_cloud_writes outcome for this item's source_uri, if any upload was attempted. */
  upload_outcome?: string | null;
  /** Failure/adopt reason from the latest upload attempt; "" when not applicable. */
  upload_message?: string | null;
  /** ISO timestamp of the latest upload attempt, or null if never attempted. */
  upload_at?: string | null;
}

export interface HmoItemPushResult {
  local_id: string;
  source_uri: string;
  status: string;
  wikibase_id: string | null;
  message: string;
}

export interface HmoItemOverridePayload {
  labels?: Record<string, string | null>;
  descriptions?: Record<string, string | null>;
  aliases?: Record<string, string[] | null>;
  add_statements?: Array<Record<string, unknown>>;
  remove_statements?: number[];
  statement_edits?: Record<string, Record<string, unknown>>;
  approved?: boolean | null;
}

export const HmoStudioItems = {
  list(runId: string): Promise<{run_id: string; items: HmoStudioItem[]}> {
    return api.get(`/runs/${runId}/hmo-studio/items`);
  },

  page(
    runId: string,
    query: {
      page: number;
      pageSize?: number;
      q?: string;
      sort?: "label" | "local_id";
      dir?: "asc" | "desc";
      filters?: Record<string, string[]>;
    },
  ): Promise<{
    run_id: string;
    page: number;
    page_size: number;
    total: number;
    facets: Record<string, Record<string, number>>;
    items: HmoStudioItem[];
  }> {
    const params = new URLSearchParams({
      page: String(query.page),
      page_size: String(query.pageSize ?? 25),
      sort: query.sort ?? "label",
      dir: query.dir ?? "asc",
    });
    if (query.q?.trim()) params.set("q", query.q.trim());
    for (const [col, values] of Object.entries(query.filters ?? {})) {
      for (const value of values) params.append("filter", `${col}:${value}`);
    }
    return api.get(`/runs/${runId}/hmo-studio/items/page?${params}`);
  },

  filteredIds(
    runId: string,
    query: {q?: string; filters?: Record<string, string[]>},
  ): Promise<{
    run_id: string;
    total: number;
    entries: Array<{local_id: string; wikibase_id: string | null; approved: boolean | null}>;
  }> {
    const params = new URLSearchParams();
    if (query.q?.trim()) params.set("q", query.q.trim());
    params.set("ids_only", "true");
    for (const [col, values] of Object.entries(query.filters ?? {})) {
      for (const value of values) params.append("filter", `${col}:${value}`);
    }
    return api.get(`/runs/${runId}/hmo-studio/items/page?${params}`);
  },

  patchOverride(
    runId: string,
    localId: string,
    payload: HmoItemOverridePayload,
  ): Promise<HmoItemOverridePayload & {run_id: string; local_id: string}> {
    return api.patch(
      `/runs/${runId}/hmo-studio/items/${encodeURIComponent(localId)}/override`,
      payload,
    );
  },

  reconcile(runId: string, localId: string): Promise<Record<string, unknown>> {
    return api.post(
      `/runs/${runId}/hmo-studio/items/${encodeURIComponent(localId)}/reconcile`,
      {},
    );
  },

  applyAiFixes(
    runId: string,
    localId: string,
    fixes: Array<Record<string, unknown>>,
  ): Promise<HmoItemOverridePayload & {run_id: string; local_id: string}> {
    return api.post(
      `/runs/${runId}/hmo-studio/items/${encodeURIComponent(localId)}/ai-fixes/apply`,
      {fixes},
    );
  },

  cachedVerdicts(runId: string): Promise<Record<string, AiVerdict>> {
    return api.get(`/runs/${runId}/hmo-studio/items/ai-verify/cached-verdicts`);
  },

  pushItem(runId: string, localId: string, allowShaclErrors = false): Promise<HmoItemPushResult> {
    const qs = allowShaclErrors ? "?allow_shacl_errors=true" : "";
    return api.post(
      `/runs/${runId}/hmo-studio/items/${encodeURIComponent(localId)}/push${qs}`,
      {},
    );
  },

  validationErrors(
    runId: string,
    onWikiOnly = false,
  ): Promise<{run_id: string; count: number; items: HmoStudioItem[]}> {
    const qs = onWikiOnly ? "?on_wiki_only=true" : "";
    return api.get(`/runs/${runId}/hmo-studio/items/validation-errors${qs}`);
  },
};
