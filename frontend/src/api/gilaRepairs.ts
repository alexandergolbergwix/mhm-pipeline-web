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
  person_role?: string;
  occurrence?: string;
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

export interface GilaCaseReview {
  decision: "verified_match" | "candidate" | "needs_evidence";
  explanation: string;
  candidate_qid: string | null;
  candidate_revision: number | null;
  source_excerpts: string[];
}

interface AuditJob {
  id: string;
  status: RunJobSnapshot["status"];
  progress: RunJobSnapshot["progress"];
  error: string | null;
}

export interface GilaPreparation extends AuditJob {
  qids: string[];
  publication_id: string | null;
  batch_counts?: {eligible: number; prepared: number} | null;
  exclusions?: Array<{qid: string; reason: string}>;
  open_case_counts?: {manual_review: number; not_checked: number} | null;
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
  people_coverage?: {
    records_with_full_marc: number;
    source_roles: number;
    roles_verified: number;
    authority_links_verified: number;
    uncertainty_preserved: number;
  };
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
    ready_count?: number;
  }>;
}

export interface GilaStatus {
  run_id: string;
  job: AuditJob | null;
  automatic_repair?: AutomaticRepairJob | null;
  report: GilaReport | null;
}

export type PreflightStatus = "eligible" | "needs_evidence" | "other_creator" | "needs_review";

export interface PreflightCase {
  case_id: string;
  points: number[];
  qid: string | null;
  control_number: string | null;
  source_field: string | null;
  reason: string;
  status: PreflightStatus;
}

export interface CorpusPreflightResult {
  records: number;
  eligible_items: number;
  requirements: Array<{point: number; counts: Partial<Record<PreflightStatus, number>>}>;
}

export interface AutomaticRepairJob extends AuditJob {
  result: {
    requirements_verified?: number;
    items_published: number;
    reference_items_corrected: number;
    repeated_references?: number;
    corpus_preflight?: CorpusPreflightResult;
    corpus_verified?: Array<{point: number; cases: number}>;
    people_batch?: {
      cases_verified?: number;
      edited_items?: number;
      excluded_creator?: number;
      unavailable_catalogues?: number;
    };
    evidence_review?: {
      checked: number;
      supported: number;
      needs_evidence: number;
      excluded_creator: number;
      unavailable: number;
    };
    requirement_comparison?: Array<{
      point: number;
      title: string;
      before: Partial<Record<AuditDisposition, number>>;
      after: Partial<Record<AuditDisposition, number>>;
    }>;
    exclusions: Array<{qid: string | null; reason: string}>;
    stages_needing_attention: Array<{stage: string; reason: string}>;
  } | null;
}

const endpoint = "/admin/wikidata-gila-repairs";
export const GilaRepairs = {
  status: () => api.get<GilaStatus>(endpoint),
  start: () => api.post<AuditJob>(endpoint + "/start", {}),
  repairAll: () => api.post<RunJobSnapshot>(endpoint + "/repair-all", {scope: "people_and_sources"}),
  preflight: (params: URLSearchParams) =>
    api.get<{items: PreflightCase[]; total: number}>(endpoint + "/preflight?" + params),
  evidence: (auditId: string) => api.get<unknown>(endpoint + "/evidence?" + new URLSearchParams({audit_id: auditId})),
  cases: (params: URLSearchParams) => api.get<{items: GilaCase[]; total: number}>(endpoint + "/cases?" + params),
  latestCaseReview: (auditId: string, caseId: string) =>
    api.get<RunJobSnapshot | null>(endpoint + "/case-review?" +
      new URLSearchParams({audit_id: auditId, case_id: caseId})),
  reviewCase: (auditId: string, caseId: string) => api.post<RunJobSnapshot>(endpoint + "/case-review", {
    audit_id: auditId, case_id: caseId,
  }),
  preparations: (offset = 0) =>
    api.get<{items: GilaPreparation[]; more: boolean}>(endpoint + "/preparations?offset=" + offset),
  prepareBatch: (auditId: string) =>
    api.post<RunJobSnapshot | null>(endpoint + "/prepare-batch", {audit_id: auditId}),
  prepare: (auditId: string, caseId: string) => api.post<RunJobSnapshot>(endpoint + "/prepare", {
    audit_id: auditId, case_id: caseId,
  }),
};
