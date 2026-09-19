import {api} from "@/api/client";

export type RuleState = "pass" | "fail" | "not_relevant" | "error";
export type RuleOverall = RuleState | "unchecked";

export interface RuleMeta {
  id: string;
  title: string;
  description: string;
  channel: string;
  uses_api: boolean;
}

export interface RuleResultEntry {
  rule_id: string;
  state: RuleState;
  field?: string;
  message?: string;
  evidence?: Record<string, unknown>;
}

export interface RuleVerdict {
  schema?: string;
  overall: RuleOverall;
  fail_count?: number;
  error_count?: number;
  checked_at?: string;
  job_id?: string | null;
  results: RuleResultEntry[];
}

/** Compact rollup shipped in the merged items list — no per-rule bodies. */
export interface RuleVerdictSummary {
  overall: RuleOverall;
  fail_count: number;
  error_count: number;
  failing_rules: string[];
  pass_count: number;
  checked_at?: string;
}

export interface RuleVerifyEntityRow {
  local_id: string;
  label: string | null;
  class_qid: string | null;
  approved: boolean | null;
  overall: RuleOverall;
  checked_at?: string | null;
  /** Pass entries are counted, not listed (payload size at 18k items). */
  pass_count: number;
  /** Non-pass entries only: fail / error / not_relevant. */
  results: RuleResultEntry[];
  /** Failing or erroring rules in the curator's blocking set. */
  blocked_by?: string[];
  /** True when no blocking-rule failure or error — ok to upload. */
  upload_ready?: boolean;
}

export interface RuleVerifyResults {
  run_id: string;
  overall_counts: Record<string, number>;
  per_rule: Record<string, Record<RuleState, number>>;
}

export interface RuleVerifyEntityPage {
  run_id: string;
  page: number;
  page_size: number;
  total: number;
  blocking_rules: string[];
  items: RuleVerifyEntityRow[];
}

export interface RuleVerifyEntityQuery {
  page: number;
  page_size?: number;
  state: string;
  rules?: string[];
  q?: string;
}

/** Single-entity shape served to the item detail drawer. */
export type RuleVerifyEntity = RuleVerifyEntityRow;

export interface RuleVerifyFilterSpec {
  state: string;
  rules?: string[];
  q?: string;
}

export interface RuleVerifySettings {
  blocked_rules: Record<string, boolean>;
  filter_presets: Array<Record<string, unknown>> | null;
}

export interface BulkApprovePreview {
  total: number;
  eligible: string[];
  excluded: Array<{local_id: string; blocked_by: Array<{rule_id: string; message: string}>}>;
  not_checked: string[];
}

export const RuleVerify = {
  catalog(runId: string): Promise<RuleMeta[]> {
    return api.get(`/runs/${runId}/hmo-studio/items/rule-verify/catalog`);
  },

  exportUrl(runId: string, format: "json" | "csv", scope: "failures" | "all" = "failures"): string {
    return `/api/runs/${runId}/hmo-studio/items/rule-verify/export?format=${format}&scope=${scope}`;
  },

  results(runId: string): Promise<RuleVerifyResults> {
    return api.get(`/runs/${runId}/hmo-studio/items/rule-verify/results`);
  },

  entityPage(
    runId: string,
    query: RuleVerifyEntityQuery,
  ): Promise<RuleVerifyEntityPage> {
    const params = new URLSearchParams({
      page: String(query.page),
      page_size: String(query.page_size ?? 50),
      state: query.state,
    });
    if (query.rules?.length) params.set("rules", query.rules.join(","));
    if (query.q?.trim()) params.set("q", query.q.trim());
    return api.get(
      `/runs/${runId}/hmo-studio/items/rule-verify/results/entities?${params}`,
    );
  },

  entity(runId: string, localId: string): Promise<RuleVerifyEntity> {
    return api.get(
      `/runs/${runId}/hmo-studio/items/rule-verify/results/entities/${encodeURIComponent(localId)}`,
    );
  },

  settings(): Promise<RuleVerifySettings> {
    return api.get("/me/rule-verify-settings");
  },

  saveSettings(payload: RuleVerifySettings): Promise<RuleVerifySettings> {
    return api.put("/me/rule-verify-settings", payload);
  },

  bulkApprovePreview(
    runId: string,
    scope: {localIds?: string[]; filters?: RuleVerifyFilterSpec},
    blockingRules?: string[],
  ): Promise<BulkApprovePreview> {
    return api.post(`/runs/${runId}/hmo-studio/items/rule-verify/bulk-approve/preview`, {
      ...(scope.localIds ? {local_ids: scope.localIds} : {}),
      ...(scope.filters ? {filters: scope.filters} : {}),
      ...(blockingRules ? {blocking_rules: blockingRules} : {}),
    });
  },

  bulkApprove(
    runId: string,
    scope: {localIds?: string[]; filters?: RuleVerifyFilterSpec},
    blockingRules?: string[],
  ): Promise<{started: boolean; eligible: number; job_id: string}> {
    return api.post(`/runs/${runId}/hmo-studio/items/rule-verify/bulk-approve`, {
      ...(scope.localIds ? {local_ids: scope.localIds} : {}),
      ...(scope.filters ? {filters: scope.filters} : {}),
      ...(blockingRules ? {blocking_rules: blockingRules} : {}),
    });
  },
};
