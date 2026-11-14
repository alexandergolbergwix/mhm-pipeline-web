import {api} from "@/api/client";

export interface SourceCandidate {
  kind: "holding" | "work_occurrence" | "catalog_reference" | "passage";
  candidate_key: string;
  control_number: string;
  source_hash: string;
  source_key: string;
  source_field: string;
  source_text: string;
  title: string;
  author?: string;
  folio?: string;
  scope_text?: string;
  scope_alignment_unproven?: boolean;
  shelfmark?: string;
  year?: number;
  pages?: string;
  status: string;
  current_targets?: string[];
  choice?: SourceChoice | null;
}

export interface SourceChoice {
  candidate: Omit<SourceCandidate, "status" | "choice" | "current_targets">;
  evidence_uri: string;
  target_qid?: string;
  rejected_qid?: string;
  decision?: "reject" | "document";
  reason?: string;
}

export interface SourceReviewRecord {
  control_number: string;
  source_hash: string;
  source_url?: string;
  counts: Record<string, number>;
  requirements: Array<{point: number; title: string; status: string}>;
  original_occurrences_available: boolean;
  candidates: SourceCandidate[];
  passages: Array<{passage_id: string; source_key: string; source_text: string; status: string}>;
  source_gaps: Array<{field: string; point: number; status: string}>;
  record_status_notes: string[];
  source_extent_text?: string;
  person_subfield_reviews?: Array<{source_field: string; reason: string}>;
  catalog_references: Array<{original_citation: string; source_field: string; review_status: string}>;
  stale_choices: Array<{choice: SourceChoice; reason: string}>;
  model_decision: {status: string; physical_manuscript_and_work: string};
}

export interface SourceChoiceRequest {
  local_id: string;
  control_number: string;
  candidate_key: string;
  expected_source_hash: string;
  target_qid?: string;
  evidence_uri?: string;
  decision?: "select" | "reject" | "document";
  reason?: string;
  discard?: boolean;
}

export const SourceReview = {
  get: (runId: string, localId: string) => api.get<{local_id: string; records: SourceReviewRecord[]}>(
    `/runs/${runId}/wikidata-studio/source-review?${new URLSearchParams({local_id: localId})}`,
  ),
  save: (runId: string, request: SourceChoiceRequest) => api.post<{rebuild_required: boolean}>(
    `/runs/${runId}/wikidata-studio/source-choice`, request,
  ),
};
