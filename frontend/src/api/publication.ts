import {api} from "@/api/client";

export type PublicationTarget = "test" | "live";
export type PublicationProjectionSource = "legacy" | "canonical";
export type PublicationStatus =
  | "preparing"
  | "ready_for_review"
  | "reviewed"
  | "dry_run_ready"
  | "publishing"
  | "paused"
  | "completed"
  | "cancelled"
  | "failed";
export type ReviewDecision = "approve" | "reject";
export type ReviewStatus = "pending" | "approved" | "rejected" | "stale";
export type OperationStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface RunPublicationSource {
  kind: "run";
  projection_source: PublicationProjectionSource;
  approved_only: boolean;
}

export interface ReferenceOnlySelection {
  publication_id: string;
  plan_id: string;
  plan_digest: string;
  entity_keys: string[];
}

export interface PreparePublicationRequest {
  reference_only?: ReferenceOnlySelection;
  profile_id: string;
  profile_version: string;
  target: PublicationTarget;
  source: RunPublicationSource;
}

export interface PublicationFindingCounts {
  error: number;
  warning: number;
  info: number;
}

export interface ReleaseSummary {
  release_id: string;
  release_digest: string;
  revision: number;
  created_at: string;
  entity_count: number;
  finding_counts: PublicationFindingCounts;
}

export interface ApprovalSetSummary {
  approval_set_id: string;
  approval_digest: string;
  release_id: string;
  release_digest: string;
  status: ReviewStatus;
  approved_count: number;
  rejected_count: number;
  pending_count: number;
  created_at: string;
}

export interface PlanSummary {
  blocked_actions?: Array<{entity_key: string; target_qid: string | null; reason: string; consent?: PublicationForeignQidConsent | null}>;
  plan_id: string;
  plan_digest: string;
  release_id: string;
  release_digest: string;
  approval_set_id: string;
  status: "ready" | "blocked" | "expired" | "executed";
  expires_at: string | null;
  action_counts: Record<string, number>;
}

export interface DryRunReceiptSummary {
  dry_run_receipt_id: string;
  receipt_digest: string;
  plan_id: string;
  plan_digest: string;
  status: "valid" | "stale" | "failed";
  checked_at: string;
  expires_at: string | null;
}

export interface ExecutionSummary {
  execution_id: string;
  plan_id: string;
  status: OperationStatus | "paused";
  processed: number;
  total: number;
  succeeded: number;
  failed: number;
  skipped: number;
  current_entity_label: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface PublicationSummary {
  publication_id: string;
  run_id: string;
  profile_id: string;
  profile_version: string;
  target: PublicationTarget;
  status: PublicationStatus;
  source_current: boolean;
  current_release: ReleaseSummary;
  approval_set: ApprovalSetSummary | null;
  plan: PlanSummary | null;
  dry_run_receipt: DryRunReceiptSummary | null;
  execution: ExecutionSummary | null;
}

export interface PublicationEntityFinding {
  finding_id: string;
  code: string;
  severity: "error" | "warning" | "info";
  message: string;
  gate: boolean;
}

export interface PublicationEntity {
  reference_only?: boolean;
  deferred?: boolean;
  policy_reason?: string;
  deferred_statements?: Array<Record<string, unknown>>;
  entity_id: string;
  entity_digest: string;
  entity_kind: string;
  label: string;
  description: string | null;
  target_qid: string | null;
  statement_count: number;
  review_status: ReviewStatus;
  proposed_action: "create" | "update" | "adopt" | "skip" | "blocked" | null;
  findings: PublicationEntityFinding[];
}

export type PublicationEntitySelection =
  | {mode: "eligible_release"}
  | {mode: "entities"; entity_keys: string[]};

export interface ReviewCommand {
  type: "review";
  release_id: string;
  expected_release_digest: string;
  selection: PublicationEntitySelection;
  decision: ReviewDecision;
  reason: string;
}

export interface PublicationForeignQidConsent {
  entity_key: string;
  qid: string;
  remote_revision: number;
  entity_digest: string;
}

export interface DryRunCommand {
  force_refresh?: boolean;
  foreign_qid_consents?: PublicationForeignQidConsent[];
  type: "dry_run";
  approval_set_id: string;
  expected_approval_digest: string;
}

export interface PublishCommand {
  type: "publish";
  plan_id: string;
  dry_run_receipt_id: string;
  expected_receipt_digest: string;
}

export interface ResumeCommand {
  type: "resume";
  execution_id: string;
}

export interface CancelCommand {
  type: "cancel";
  operation_id?: string;
  reason?: string;
}

export type PublicationAdvanceCommand =
  | ReviewCommand
  | DryRunCommand
  | PublishCommand
  | ResumeCommand
  | CancelCommand;

export interface PublicationOperation {
  operation_id: string;
  command: "prepare" | PublicationAdvanceCommand["type"];
  status: OperationStatus;
  progress: ExecutionSummary | null;
  error: string | null;
}

export interface PreparePublicationResponse {
  publication: PublicationSummary | null;
  operation?: PublicationOperation | null;
}

export interface AdvancePublicationResponse {
  publication: PublicationSummary;
  operation?: PublicationOperation | null;
}

export interface PublicationSummaryQuery {
  type: "summary";
}

export interface PublicationEntitiesQuery {
  type: "entities";
  release_id: string;
  cursor?: string | null;
  limit: number;
  entity_kind?: string;
  review_status?: ReviewStatus;
  query?: string;
}

export interface PublicationOperationQuery {
  type: "operation";
  operation_id: string;
}

export interface PublicationAuditQuery {
  type: "audit";
  cursor?: string | null;
  limit: number;
}

export type PublicationReadQuery =
  | PublicationSummaryQuery
  | PublicationEntitiesQuery
  | PublicationOperationQuery
  | PublicationAuditQuery;

export interface PublicationSummaryRead {
  publication: PublicationSummary;
}

export interface PublicationEntityPage {
  release_id: string;
  release_digest: string;
  items: PublicationEntity[];
  next_cursor: string | null;
  total: number;
}

export interface PublicationOperationRead {
  operation: PublicationOperation;
  publication: PublicationSummary;
}

export interface PublicationAuditEvent {
  event_id: string;
  sequence: number;
  event_type: string;
  occurred_at: string;
  actor_label: string | null;
  release_id: string | null;
  entity_id: string | null;
  message: string;
  details: Record<string, unknown>;
}

export interface PublicationAuditPage {
  publication_id: string;
  items: PublicationAuditEvent[];
  next_cursor: string | null;
  total: number;
}

export type PublicationReadResponse =
  | PublicationSummaryRead
  | PublicationEntityPage
  | PublicationOperationRead
  | PublicationAuditPage;

function publicationPath(runId: string, publicationId?: string): string {
  const run = encodeURIComponent(runId);
  if (!publicationId) return `/runs/${run}/wikidata-publications/prepare`;
  return `/runs/${run}/wikidata-publications/${encodeURIComponent(publicationId)}`;
}

function readPublication(
  runId: string,
  publicationId: string,
  query: PublicationSummaryQuery,
): Promise<PublicationSummaryRead>;
function readPublication(
  runId: string,
  publicationId: string,
  query: PublicationEntitiesQuery,
): Promise<PublicationEntityPage>;
function readPublication(
  runId: string,
  publicationId: string,
  query: PublicationOperationQuery,
): Promise<PublicationOperationRead>;
function readPublication(
  runId: string,
  publicationId: string,
  query: PublicationAuditQuery,
): Promise<PublicationAuditPage>;
function readPublication(
  runId: string,
  publicationId: string,
  query: PublicationReadQuery,
): Promise<PublicationReadResponse> {
  return api.post<PublicationReadResponse>(
    `${publicationPath(runId, publicationId)}/read`,
    {query},
  );
}

export const PublicationApi = {
  latest: (runId: string) => api.get<PreparePublicationResponse>(`/runs/${encodeURIComponent(runId)}/wikidata-publications/latest`),
  prepare: (runId: string, request: PreparePublicationRequest) =>
    api.post<PreparePublicationResponse>(publicationPath(runId), request),

  advance: (
    runId: string,
    publicationId: string,
    command: PublicationAdvanceCommand,
  ) => api.post<AdvancePublicationResponse>(
    `${publicationPath(runId, publicationId)}/advance`,
    {command},
  ),

  read: readPublication,
};

export interface PublicationAiReviewState {
  phase?: string | null;
  message?: string | null;
  job_id: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | null;
  processed: number;
  total: number;
  error: string | null;
  report: {
    publication_id: string;
    plan_id: string;
    plan_digest: string;
    release_digest: string;
    tier_model: string;
    created_at: string;
    automatic?: boolean;
    policy_version?: string;
    result_publication_id?: string | null;
    items: Array<{
      entity_key: string;
      label: string;
      qid: string | null;
      status: "recommended" | "review_required" | "lookup_resolved" | "error" | "reuse_existing" | "create" | "deferred";
      reason: string;
      resolution?: {retryable?: boolean};
      consent: PublicationForeignQidConsent | null;
    }>;
  } | null;
}

export const PublicationAiReviewApi = {
  read: (runId: string, publicationId: string) =>
    api.get<PublicationAiReviewState>(`${publicationPath(runId, publicationId)}/ai-review`),
  start: (runId: string, publicationId: string, request: {
    plan_id: string; plan_digest: string; tier_model: string; force_refresh: boolean;
    automatic?: boolean; verification_model?: string;
  }) => api.post<PublicationAiReviewState>(`${publicationPath(runId, publicationId)}/ai-review`, request),
};
