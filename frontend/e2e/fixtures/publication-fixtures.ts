import type {PublicationSummary} from "../../src/api/publication";
import {TEST_RUN_ID} from "./wikidata-fixtures";

export const RELEASE = {
  release_id: "release-7",
  release_digest: "sha256:release-7",
  revision: 7,
  created_at: "2026-09-05T10:00:00Z",
  entity_count: 2,
  finding_counts: {error: 0, warning: 0, info: 0},
};

export function summary(stage: "prepare" | "review" | "dry_run" | "publish"): PublicationSummary {
  const approval = stage === "review" || stage === "dry_run" || stage === "publish"
    ? {
        approval_set_id: "approval-7",
        approval_digest: "sha256:approval-7",
        release_id: RELEASE.release_id,
        release_digest: RELEASE.release_digest,
        status: "approved" as const,
        approved_count: 2,
        rejected_count: 0,
        pending_count: 0,
        created_at: "2026-09-05T10:01:00Z",
      }
    : null;
  const plan = stage === "dry_run" || stage === "publish"
    ? {
        plan_id: "plan-7",
        plan_digest: "sha256:plan-7",
        release_id: RELEASE.release_id,
        release_digest: RELEASE.release_digest,
        approval_set_id: "approval-7",
        status: "ready" as const,
        expires_at: "2099-09-05T11:00:00Z",
        action_counts: {create: 2},
      }
    : null;
  const dryRunReceipt = stage === "dry_run" || stage === "publish"
    ? {
        dry_run_receipt_id: "receipt-7",
        receipt_digest: "sha256:receipt-7",
        plan_id: "plan-7",
        plan_digest: "sha256:plan-7",
        status: "valid" as const,
        checked_at: "2026-09-05T10:02:00Z",
        expires_at: "2099-09-05T10:30:00Z",
      }
    : null;
  return {
    publication_id: "publication-7",
    run_id: TEST_RUN_ID,
    profile_id: "mhm-wikidata",
    profile_version: "1-nodes",
    target: "live",
    status: stage === "publish" ? "publishing" : stage === "dry_run" ? "dry_run_ready" : "reviewed",
    source_current: true,
    current_release: RELEASE,
    approval_set: approval,
    plan,
    dry_run_receipt: dryRunReceipt,
    execution: stage === "publish"
      ? {
          execution_id: "execution-7",
          plan_id: "plan-7",
          status: "running",
          processed: 25,
          total: 100000,
          succeeded: 24,
          failed: 0,
          skipped: 1,
          current_entity_label: "Work 26",
          started_at: "2026-09-05T10:03:00Z",
          finished_at: null,
        }
      : null,
  };
}
