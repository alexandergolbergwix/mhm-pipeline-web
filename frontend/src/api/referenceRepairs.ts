import {api} from "@/api/client";

export interface ReferenceRepairJob {
  id: string;
  project_id?: string;
  status: string;
  progress: {
    phase?: string;
    message?: string;
    current_qid?: string;
    next_attempt_at?: string;
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
  verified: number;
  completed?: number;
  already_clean?: number;
  observed_at?: string;
  worker_heartbeat_at?: string | null;
  recent_results?: Array<{qid: string; status: string; after_revision: number | null}>;
  halted_status: string | null;
  job: ReferenceRepairJob | null;
}

const endpoint = "/admin/wikidata-reference-repairs";
export const ReferenceRepairs = {
  status: () => api.get<ReferenceRepairStatus>(endpoint),
  start: (scopeDigest: string) => api.post<ReferenceRepairJob>(endpoint + "/start", {
    confirmed: true, scope_digest: scopeDigest,
  }),
};
