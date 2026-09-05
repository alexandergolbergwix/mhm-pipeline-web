import {expect, test} from "@playwright/test";

import type {PublicationSummary} from "../src/api/publication";
import {
  installStudioMocks,
  makeBuildResponse,
  TEST_RUN_ID,
} from "./fixtures/wikidata-fixtures";

const RELEASE = {
  release_id: "release-7",
  release_digest: "sha256:release-7",
  revision: 7,
  created_at: "2026-09-05T10:00:00Z",
  entity_count: 2,
  finding_counts: {error: 0, warning: 0, info: 0},
};

function summary(stage: "review" | "dry_run" | "publish"): PublicationSummary {
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
    profile_version: "1",
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

async function gotoModernStudio(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    localStorage.setItem("mhm.studio.reviewMode", "modern");
  });
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await expect(page.getByTestId("publication-prepare")).toBeVisible();
}

async function installAuditShellMocks(page: import("@playwright/test").Page) {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "user-1",
        email: "curator@example.com",
        name: "Test Curator",
        role: "editor",
      }),
    });
  });
  await page.route("**/api/jobs/mine**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({jobs: []}),
    });
  });
}

test("a curator reviews a Release, creates a Dry-run Receipt, then publishes", async ({page}) => {
  test.setTimeout(20_000);
  await installStudioMocks(page, makeBuildResponse());
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs?active=true`, async (route) => {
    await route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify({jobs: []})});
  });
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs?kind=wikidata_verify&limit=5`, async (route) => {
    await route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify({jobs: []})});
  });
  const commands: Array<Record<string, unknown>> = [];
  let prepareBody: Record<string, unknown> | null = null;
  let current = summary("review");

  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-publications/prepare`, async (route) => {
    prepareBody = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({publication: current}),
    });
  });
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-publications/publication-7/read`, async (route) => {
    const body: {query?: {type?: string}} = route.request().postDataJSON();
    if (body.query?.type === "entities") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          release_id: RELEASE.release_id,
          release_digest: RELEASE.release_digest,
          items: [
            {
              entity_id: "work-1",
              entity_digest: "sha256:work-1",
              entity_kind: "work",
              label: "Work 1",
              description: null,
              target_qid: null,
              statement_count: 2,
              review_status: "pending",
              proposed_action: "create",
              findings: [],
            },
            {
              entity_id: "work-2",
              entity_digest: "sha256:work-2",
              entity_kind: "work",
              label: "Work 2",
              description: null,
              target_qid: null,
              statement_count: 2,
              review_status: "pending",
              proposed_action: "create",
              findings: [],
            },
          ],
          next_cursor: null,
          total: 2,
        }),
      });
      return;
    }
    await route.fulfill({status: 400, contentType: "application/json", body: JSON.stringify({detail: "Unexpected query"})});
  });
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-publications/publication-7/advance`, async (route) => {
    const body: {command?: Record<string, unknown>} = route.request().postDataJSON();
    if (body.command) {
      commands.push(body.command);
      if (body.command.type === "dry_run") current = summary("dry_run");
      if (body.command.type === "publish") current = summary("publish");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({publication: current}),
      });
      return;
    }
    await route.fulfill({status: 400, contentType: "application/json", body: JSON.stringify({detail: "Missing command"})});
  });

  await gotoModernStudio(page);
  await page.getByTestId("publication-target-live").check();
  await page.getByTestId("publication-prepare").click({force: true});

  await expect(page.getByTestId("publication-release-state")).toContainText("Release 7");
  expect(prepareBody).toEqual({
    profile_id: "mhm-wikidata",
    profile_version: "1",
    target: "live",
    source: {kind: "run", projection_source: "canonical", approved_only: true},
  });
  await expect(page.getByTestId("wikidata-item-upload-actions")).toBeHidden();
  await expect(page.getByTestId("publication-publish")).toBeDisabled();
  await expect(page.getByText("Upload anyway")).toHaveCount(0);

  await page.getByTestId("publication-review-eligible-release").click({force: true});
  await expect(page.getByTestId("publication-approval-state")).toContainText("current");
  await expect(page.getByTestId("publication-dry-run")).toBeEnabled();
  await page.getByTestId("publication-dry-run").click({force: true});
  await expect(page.getByTestId("publication-dry-run-receipt-state")).toContainText("current");
  await expect(page.getByTestId("publication-publish")).toBeEnabled();
  await page.getByTestId("publication-publish").click({force: true});

  await expect(page.getByTestId("publication-execution-progress")).toContainText("25 of 100,000");
  expect(commands.map((command) => command.type)).toEqual(["review", "dry_run", "publish"]);
  expect(commands[0]).toEqual({
    type: "review",
    release_id: RELEASE.release_id,
    expected_release_digest: RELEASE.release_digest,
    selection: {mode: "eligible_release"},
    decision: "approve",
    reason: "Curator approved the eligible Release manifest.",
  });
  expect(commands[2]).toEqual({
    type: "publish",
    plan_id: "plan-7",
    dry_run_receipt_id: "receipt-7",
    expected_receipt_digest: "sha256:receipt-7",
  });
});

test("the audit page reads a bounded Publication audit page", async ({page}) => {
  test.setTimeout(20_000);
  await installAuditShellMocks(page);
  let auditQuery: Record<string, unknown> | null = null;
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-publications/publication-7/read`, async (route) => {
    const body: {query?: Record<string, unknown>} = route.request().postDataJSON();
    auditQuery = body.query ?? null;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        publication_id: "publication-7",
        items: [{
          event_id: "event-7",
          sequence: 7,
          event_type: "execution.completed",
          occurred_at: "2026-09-05T10:10:00Z",
          actor_label: "Test Curator",
          release_id: "release-7",
          entity_id: null,
          message: "Execution completed with 100,000 Write Receipts.",
          details: {write_receipts: 100000},
        }],
        next_cursor: null,
        total: 1,
      }),
    });
  });

  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio/publications/publication-7/audit`);

  await expect(page.getByText("Execution completed with 100,000 Write Receipts.")).toBeVisible();
  await expect.poll(() => auditQuery).toEqual({type: "audit", cursor: null, limit: 100});
});
