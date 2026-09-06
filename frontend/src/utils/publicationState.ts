import type {PublicationSummary} from "@/api/publication";

export interface PublicationReadiness {
  approvalCurrent: boolean;
  planCurrent: boolean;
  receiptCurrent: boolean;
  publishAllowed: boolean;
  approvalReason: string;
  receiptReason: string;
}

function hasExpired(value: string | null, nowMs: number): boolean {
  if (!value) return false;
  const expiresAt = Date.parse(value);
  return Number.isFinite(expiresAt) && expiresAt <= nowMs;
}

export function getPublicationReadiness(
  publication: PublicationSummary,
  nowMs = Date.now(),
): PublicationReadiness {
  const release = publication.current_release;
  const approval = publication.approval_set;
  const approvalBindsRelease = approval !== null
    && approval.release_id === release.release_id
    && approval.release_digest === release.release_digest;
  const approvalCurrent = approvalBindsRelease
    && approval.status === "approved"
    && approval.pending_count === 0;

  const plan = publication.plan;
  const planCurrent = approvalCurrent
    && plan !== null
    && plan.release_id === release.release_id
    && plan.release_digest === release.release_digest
    && plan.approval_set_id === approval?.approval_set_id
    && plan.status === "ready"
    && !hasExpired(plan.expires_at, nowMs);

  const receipt = publication.dry_run_receipt;
  const receiptCurrent = planCurrent
    && receipt !== null
    && receipt.plan_id === plan?.plan_id
    && receipt.plan_digest === plan?.plan_digest
    && receipt.status === "valid"
    && !hasExpired(receipt.expires_at, nowMs);

  let approvalReason = "Review the current Release.";
  if (approval && !approvalBindsRelease) approvalReason = "The Approval Set is stale.";
  else if (approval?.status === "stale") approvalReason = "The Approval Set is stale.";
  else if (approvalCurrent) approvalReason = "The Approval Set is current.";
  else if (approval && approval.pending_count > 0) {
    approvalReason = `${approval.pending_count.toLocaleString()} entities need review.`;
  }

  let receiptReason = "Run the Plan without writes before publication.";
  if (receipt?.status === "failed") receiptReason = "The dry-run has blocked actions. Review the plan below.";
  else if (receipt && !receiptCurrent) receiptReason = "The Dry-run Receipt is stale.";
  else if (receiptCurrent) receiptReason = "The Dry-run Receipt is current.";

  return {
    approvalCurrent,
    planCurrent,
    receiptCurrent,
    publishAllowed: receiptCurrent && release.finding_counts.error === 0,
    approvalReason,
    receiptReason,
  };
}
