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
  wikibase_id: string | null;
  status: string | null;
  approved: boolean | null;
  overall: RuleOverall;
  checked_at?: string | null;
  /** Pass entries are counted, not listed (payload size at 18k items). */
  pass_count: number;
  /** Non-pass entries only: fail / error / not_relevant. */
  results: RuleResultEntry[];
}

export interface RuleVerifyResults {
  run_id: string;
  overall_counts: Record<string, number>;
  per_rule: Record<string, Record<RuleState, number>>;
  items: RuleVerifyEntityRow[];
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

  results(runId: string): Promise<RuleVerifyResults> {
    return api.get(`/runs/${runId}/hmo-studio/items/rule-verify/results`);
  },

  settings(): Promise<RuleVerifySettings> {
    return api.get("/me/rule-verify-settings");
  },

  saveSettings(payload: RuleVerifySettings): Promise<RuleVerifySettings> {
    return api.put("/me/rule-verify-settings", payload);
  },

  bulkApprovePreview(
    runId: string,
    localIds: string[],
    blockingRules?: string[],
  ): Promise<{
    total: number;
    eligible: string[];
    excluded: Array<{local_id: string; blocked_by: Array<{rule_id: string; message: string}>}>;
    not_checked: string[];
  }> {
    return api.post(`/runs/${runId}/hmo-studio/items/rule-verify/bulk-approve/preview`, {
      local_ids: localIds,
      ...(blockingRules ? {blocking_rules: blockingRules} : {}),
    });
  },

  bulkApprove(
    runId: string,
    localIds: string[],
    blockingRules?: string[],
  ): Promise<{started: boolean; eligible: number; job_id: string}> {
    return api.post(`/runs/${runId}/hmo-studio/items/rule-verify/bulk-approve`, {
      local_ids: localIds,
      ...(blockingRules ? {blocking_rules: blockingRules} : {}),
    });
  },
};
