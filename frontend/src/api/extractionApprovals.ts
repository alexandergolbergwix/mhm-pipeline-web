/**
 * ExtractionApprovals API client.
 *
 * Typed wrapper around the backend's per-entity approval, bulk approve,
 * and auto-approve endpoints. Mirrors the AI Extraction (AI-based Enrichment)
 * editor data model the PyQt6 app uses, including the eval-agent AI
 * verdict carried on each entity (CLAUDE.md Rule W-13).
 *
 * NOTE on the `ai_verdict.overall` field: same shape as
 * AuthorityMatch.payload.ai_verdict (Rule W-13). Accepts both "pass"
 * and "full" as synonyms — the AiVerdictPill collapses them.
 */

import { api } from "@/api/client";

export const ENTITY_TYPES = [
  "PERSON",
  "OWNER",
  "DATE",
  "COLLECTION",
  "WORK",
  "WORK_AUTHOR",
  "FOLIO",
  "PLACE",
  "ORG",
  "GENRE",
  "OTHER",
] as const;
export type EntityType = (typeof ENTITY_TYPES)[number];

export const ENTITY_ROLES = [
  "AUTHOR",
  "TRANSCRIBER",
  "OWNER",
  "CENSOR",
  "TRANSLATOR",
  "COMMENTATOR",
  "SCRIBE",
  "SUBJECT",
  "",
] as const;
export type EntityRole = (typeof ENTITY_ROLES)[number];

export const ENTITY_SOURCES = [
  "person_ner",
  "provenance_ner",
  "contents_ner",
  "genre_ml",
  "manual",
] as const;
export type EntitySource = (typeof ENTITY_SOURCES)[number];

export type AiVerdictOverall =
  | "pass"
  | "full"
  | "partial"
  | "fail"
  | "abstain"
  | "unknown";

/** v2 extension: AI-suggested corrected entity text.
 *  Only present on NER entities (person_ner / provenance_ner / contents_ner).
 *  Never on genre_ml entities. */
export interface AiSuggestedFix {
  text: string;
  reasoning?: string | null;
  source_field?: string | null;
  confidence: "high";
}

export interface AiVerdict {
  overall: AiVerdictOverall;
  name_ok?: boolean | null;
  type_ok?: boolean | null;
  role_ok?: boolean | null;
  reasoning?: string | null;
  model?: string | null;
  judged_at?: string | null;
  cache_key?: string | null;
  session_id?: string | null;
  evaluator?: string | null;
  novel?: boolean;
  /** v2: Gemini-proposed corrected entity text, or null when no fix applies. */
  suggested_fix?: AiSuggestedFix | null;
}

export interface ExistsIn {
  status: "grounded" | "wrong_field" | "novel" | "unknown";
  fields: string[];
  note: string;
}

export interface Entity {
  id: string;
  /** Postgres ``ExtractionApproval.id`` — use for history API routes. */
  approval_row_id?: string;
  control_number: string;
  text: string;
  effective_text?: string;
  type: EntityType;
  role: EntityRole;
  effective_type?: string;
  effective_role?: string;
  source: EntitySource;
  confidence: number | null;
  model_confidence: number | null;
  start: number | null;
  end: number | null;
  approved: boolean;
  rejected: boolean;
  override_type?: string | null;
  override_role?: string | null;
  override_text?: string | null;
  exists_in: ExistsIn | null;
  ai_verdict: AiVerdict | null;
}

export interface EntityListResponse {
  entities: Entity[];
  total: number;
  approved_count?: number;
  /** Distinct control numbers across the full run. */
  record_count?: number;
  /** Per-source entity counts across the full run, e.g.
   *  {person_ner: 40, provenance_ner: 12, contents_ner: 30, genre_ml: 8}. */
  source_counts?: Record<string, number>;
}

export interface AutoApproveRule {
  min_confidence: number;
  sources: EntitySource[];
  types: EntityType[];
  not_roles: EntityRole[];
  require_ai_pass: boolean;
  respect_ai_fail: boolean;
}

export interface AutoApproveResult {
  matched: number;
  approved: number;
}

export interface BulkApproveResult {
  updated: number;
}

export interface EntityPatch {
  text?: string;
  type?: EntityType;
  role?: EntityRole;
  approved?: boolean;
  rejected?: boolean;
  start?: number | null;
  end?: number | null;
}

const base = (runId: string): string =>
  `/runs/${encodeURIComponent(runId)}/extraction/entities`;

export const ExtractionApprovals = {
  list: (runId: string): Promise<EntityListResponse> =>
    api.get<EntityListResponse>(base(runId)),

  patch: (
    runId: string,
    entityId: string,
    patch: EntityPatch,
  ): Promise<Entity> =>
    api.patch<Entity>(
      `${base(runId)}/${encodeURIComponent(entityId)}`,
      {
        approved:      patch.approved,
        override_type: patch.type,
        override_role: patch.role,
        override_text: patch.text,
      },
    ),

  bulkApprove: (
    runId: string,
    entityIds: string[],
    approved: boolean,
  ): Promise<BulkApproveResult> =>
    api.post<BulkApproveResult>(`${base(runId)}/bulk-approve`, {
      entity_ids: entityIds,
      approved,
    }),

  autoApprove: (
    runId: string,
    rule: AutoApproveRule,
  ): Promise<AutoApproveResult> =>
    api.post<AutoApproveResult>(`${base(runId)}/auto-approve`, rule),

  previewAutoApprove: (
    runId: string,
    rule: AutoApproveRule,
  ): Promise<AutoApproveResult> =>
    api.post<AutoApproveResult>(`${base(runId)}/auto-approve/preview`, rule),
};

export const DEFAULT_AUTO_APPROVE_RULE: AutoApproveRule = {
  min_confidence: 0.85,
  sources: [...ENTITY_SOURCES],
  types: [...ENTITY_TYPES],
  not_roles: [],
  require_ai_pass: false,
  respect_ai_fail: true,
};
