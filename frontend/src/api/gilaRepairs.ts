import {api} from "@/api/client";
import type {RunJobSnapshot} from "@/api/runJobs";

export type AuditDisposition = "verified" | "repair_required" | "manual_review" | "not_checked";
export type CaseFilter = Exclude<AuditDisposition, "verified"> | "absent_in_source" | "ready_to_prepare";

export interface GilaCase {
  case_id: string;
  points: number[];
  disposition: Exclude<AuditDisposition, "verified">;
  classification?: string;
  qid?: string;
  qids?: string[];
  control_number?: string;
  source_field?: string;
  source_value?: string;
  expected_author?: string;
  expected_value?: string;
  expected_qualifiers?: Record<string, string>;
  live_revision?: number;
  reason?: string;
  live_reason?: string;
  completion_reason?: string;
  local_id?: string;
  can_prepare?: boolean;
}

interface AuditJob {
  id: string;
  status: RunJobSnapshot["status"];
  progress: RunJobSnapshot["progress"];
  error: string | null;
}

export interface GilaReport {
  id: string;
  finished_at: string;
  build_revision: string;
  record_count: number;
  public_requested: number;
  public_read: number;
  reference_items: number;
  reference_blocks: number;
  case_count: number;
  all_requirements_verified: boolean;
  requirement_coverage: {
    public_reads: number;
    verified_claims: number;
    source_gaps: number;
    unchecked_passages: number;
    model_decision: string;
  };
  email_points: Array<{
    point: number; title: string; status: AuditDisposition;
    counts: Partial<Record<AuditDisposition, number>>;
  }>;
}

export interface GilaStatus {
  run_id: string;
  job: AuditJob | null;
  report: GilaReport | null;
}

const endpoint = "/admin/wikidata-gila-repairs";
export const GilaRepairs = {
  status: () => api.get<GilaStatus>(endpoint),
  start: () => api.post<AuditJob>(endpoint + "/start", {}),
  evidence: (auditId: string) => api.get<unknown>(endpoint + "/evidence?" + new URLSearchParams({audit_id: auditId})),
  cases: (params: URLSearchParams) => api.get<{items: GilaCase[]; total: number}>(endpoint + "/cases?" + params),
  prepare: (auditId: string, caseId: string) => api.post<RunJobSnapshot>(endpoint + "/prepare", {
    audit_id: auditId, case_id: caseId,
  }),
};
