import {api} from "@/api/client";

export type ReferenceScope = "original" | "remaining";

export interface ReferenceRepairJob {
  id: string;
  project_id?: string;
  status: string;
  progress: {
    phase?: string;
    message?: string;
    current_qid?: string;
    next_attempt_at?: string;
    lag_probe_status?: "ready" | "delayed" | "unavailable" | null;
    lag_probe_seconds?: number | null;
    next_lag_probe_at?: string | null;
    mirror_scan?: {
      status?: "scanning" | "complete" | "unavailable";
      reviewed?: number;
      total?: number;
      candidates?: number;
      seen_clean?: number;
      unchecked?: number;
    };
  };
  created_at?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  error?: string | null;
}

export interface ReferenceRepairStatus {
  enabled: boolean;
  scope_digest: string;
  run_id: string;
  project_id?: string;
  total: number;
  eta_seconds?: number | null;
  verified: number;
  maxlag: number;
  completed?: number;
  already_clean?: number;
  observed_at?: string;
  worker_heartbeat_at?: string | null;
  recent_results?: Array<{qid: string; status: string; after_revision: number | null}>;
  halted_status: string | null;
  halted_qid?: string | null;
  resume_action?: "resume" | "check_repair" | "review" | null;
  job: ReferenceRepairJob | null;
  other_scope_active?: boolean;
  reference_inventory?: {
    audited_at: string;
    live_checked_at?: string | null;
    items: Array<{
      qid: string; baseline_revision: number; baseline_blocks: number; creator_matches: boolean;
      observed_blocks: number | null; queue_result: string | null;
    }>;
  } | null;
}

const endpoint = "/admin/wikidata-reference-repairs";
export const ReferenceRepairs = {
  status: (scope: ReferenceScope) => api.get<ReferenceRepairStatus>(
    scope === "original" ? endpoint : endpoint + "?scope=remaining",
  ),
  start: (scopeDigest: string, scope: ReferenceScope) => api.post<ReferenceRepairJob>(endpoint + "/start", {
    confirmed: true, scope_digest: scopeDigest, ...(scope === "remaining" ? {scope} : {}),
  }),
};
