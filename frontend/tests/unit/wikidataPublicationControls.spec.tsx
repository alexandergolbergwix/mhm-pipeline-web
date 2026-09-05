import {render, screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {describe, expect, it, vi} from "vitest";

import type {
  ApprovalSetSummary,
  DryRunReceiptSummary,
  PlanSummary,
  PublicationEntity,
  PublicationSummary,
} from "@/api/publication";
import {WikidataPublicationControls} from "@/components/wikidata/WikidataPublicationControls";

function entity(id: string): PublicationEntity {
  return {
    entity_id: id,
    entity_digest: `sha256:${id}`,
    entity_kind: "work",
    label: id,
    description: null,
    target_qid: null,
    statement_count: 2,
    review_status: "pending",
    proposed_action: "create",
    findings: [],
  };
}

function approval(overrides: Partial<ApprovalSetSummary> = {}): ApprovalSetSummary {
  return {
    approval_set_id: "approval-1",
    approval_digest: "sha256:approval-1",
    release_id: "release-3",
    release_digest: "sha256:release-3",
    status: "approved",
    approved_count: 2,
    rejected_count: 0,
    pending_count: 0,
    created_at: "2026-09-05T10:00:00Z",
    ...overrides,
  };
}

function plan(overrides: Partial<PlanSummary> = {}): PlanSummary {
  return {
    plan_id: "plan-1",
    plan_digest: "sha256:plan-1",
    release_id: "release-3",
    release_digest: "sha256:release-3",
    approval_set_id: "approval-1",
    status: "ready",
    expires_at: "2099-09-05T10:00:00Z",
    action_counts: {create: 2},
    ...overrides,
  };
}

function receipt(overrides: Partial<DryRunReceiptSummary> = {}): DryRunReceiptSummary {
  return {
    dry_run_receipt_id: "receipt-1",
    receipt_digest: "sha256:receipt-1",
    plan_id: "plan-1",
    plan_digest: "sha256:plan-1",
    status: "valid",
    checked_at: "2026-09-05T10:01:00Z",
    expires_at: "2099-09-05T10:30:00Z",
    ...overrides,
  };
}

function publication(overrides: Partial<PublicationSummary> = {}): PublicationSummary {
  return {
    publication_id: "publication-1",
    run_id: "run-1",
    profile_id: "mhm-wikidata",
    profile_version: "1",
    target: "live",
    status: "ready_for_review",
    source_current: true,
    current_release: {
      release_id: "release-3",
      release_digest: "sha256:release-3",
      revision: 3,
      created_at: "2026-09-05T10:00:00Z",
      entity_count: 2,
      finding_counts: {error: 0, warning: 1, info: 0},
    },
    approval_set: null,
    plan: null,
    dry_run_receipt: null,
    execution: null,
    ...overrides,
  };
}

describe("WikidataPublicationControls", () => {
  it("binds an eligible Release selection to the Release digest", async () => {
    const onAdvance = vi.fn();
    render(<WikidataPublicationControls
      publication={publication()}
      entities={[entity("work-1"), entity("work-2")]}
      onAdvance={onAdvance}
      auditHref="/audit"
    />);

    expect(screen.getByTestId("publication-release-state")).toHaveTextContent("Release 3");
    expect(screen.getByTestId("publication-dry-run")).toBeDisabled();
    expect(screen.getByTestId("publication-publish")).toBeDisabled();

    await userEvent.click(screen.getByTestId("publication-review-eligible-release"));

    expect(onAdvance).toHaveBeenCalledWith({
      type: "review",
      release_id: "release-3",
      expected_release_digest: "sha256:release-3",
      selection: {mode: "eligible_release"},
      decision: "approve",
      reason: "Curator approved the eligible Release manifest.",
    });
    expect(screen.queryByText("Upload anyway")).not.toBeInTheDocument();
  });

  it("requires a current Approval Set before the dry run", async () => {
    const onAdvance = vi.fn();
    const current = publication({
      status: "reviewed",
      approval_set: approval(),
      plan: plan(),
    });
    render(<WikidataPublicationControls
      publication={current}
      entities={[entity("work-1"), entity("work-2")]}
      onAdvance={onAdvance}
      auditHref="/audit"
    />);

    expect(screen.getByTestId("publication-approval-state")).toHaveTextContent("current");
    expect(screen.getByTestId("publication-dry-run")).toBeEnabled();
    expect(screen.getByTestId("publication-publish")).toBeDisabled();

    await userEvent.click(screen.getByTestId("publication-dry-run"));
    expect(onAdvance).toHaveBeenCalledWith({
      type: "dry_run",
      approval_set_id: "approval-1",
      expected_approval_digest: "sha256:approval-1",
    });
  });

  it("allows publication only with a current Dry-run Receipt", async () => {
    const onAdvance = vi.fn();
    const ready = publication({
      status: "dry_run_ready",
      approval_set: approval(),
      plan: plan(),
      dry_run_receipt: receipt(),
    });
    render(<WikidataPublicationControls
      publication={ready}
      entities={[]}
      onAdvance={onAdvance}
      auditHref="/audit"
    />);

    expect(screen.getByTestId("publication-dry-run-receipt-state")).toHaveTextContent("current");
    expect(screen.getByTestId("publication-publish")).toBeEnabled();
    await userEvent.click(screen.getByTestId("publication-publish"));
    expect(onAdvance).toHaveBeenCalledWith({
      type: "publish",
      plan_id: "plan-1",
      dry_run_receipt_id: "receipt-1",
      expected_receipt_digest: "sha256:receipt-1",
    });
  });

  it("fails closed when an Approval Set has another Release digest", () => {
    render(<WikidataPublicationControls
      publication={publication({approval_set: approval({release_digest: "sha256:old"})})}
      entities={[]}
      onAdvance={vi.fn()}
      auditHref="/audit"
    />);

    expect(screen.getByTestId("publication-approval-state")).toHaveTextContent("stale");
    expect(screen.getByTestId("publication-dry-run")).toBeDisabled();
    expect(screen.getByTestId("publication-publish")).toBeDisabled();
  });

  it("marks the Release stale when the current source revision changes", () => {
    render(<WikidataPublicationControls
      publication={publication({
        source_current: false,
        approval_set: approval(),
        plan: plan(),
        dry_run_receipt: receipt(),
      })}
      entities={[]}
      onAdvance={vi.fn()}
      auditHref="/audit"
    />);

    expect(screen.getByTestId("publication-release-state")).toHaveTextContent("stale");
    expect(screen.getByTestId("publication-dry-run")).toBeDisabled();
    expect(screen.getByTestId("publication-publish")).toBeDisabled();
  });

  it("shows aggregate Execution progress without an entity list", () => {
    render(<WikidataPublicationControls
      publication={publication({
        status: "publishing",
        approval_set: approval(),
        plan: plan(),
        dry_run_receipt: receipt(),
        execution: {
          execution_id: "execution-1",
          plan_id: "plan-1",
          status: "running",
          processed: 4200,
          total: 100000,
          succeeded: 4190,
          failed: 3,
          skipped: 7,
          current_entity_label: "MS 4201",
          started_at: "2026-09-05T10:05:00Z",
          finished_at: null,
        },
      })}
      entities={[]}
      onAdvance={vi.fn()}
      auditHref="/audit"
    />);

    expect(screen.getByTestId("publication-execution-progress")).toHaveTextContent("4,200 of 100,000");
    expect(screen.getByTestId("publication-execution-progress")).toHaveTextContent("4,190 succeeded");
    expect(screen.getByTestId("publication-execution-progress")).toHaveTextContent("3 failed");
    expect(screen.getByTestId("publication-execution-progress")).toHaveTextContent("7 skipped");
  });
});
