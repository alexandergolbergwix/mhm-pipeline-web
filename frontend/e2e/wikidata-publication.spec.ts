import {expect, test} from "@playwright/test";

import type {PublicationSummary} from "../src/api/publication";
import {
  installStudioMocks,
  makeBuildResponse,
  TEST_RUN_ID,
} from "./fixtures/wikidata-fixtures";

test.beforeEach(async ({page}) => {
  await page.route("**/ai-review", (route) => route.fulfill({json: {job_id: null, status: null, processed: 0, total: 0, report: null, error: null}}));
  await page.route("**/wikidata-publications/latest", async (route) => {
    await route.fulfill({json: {publication: null}});
  });
  await page.route("**/jobs?kind=wikidata_publication_dry_run&limit=1", async (route) => {
    await route.fulfill({json: {jobs: []}});
  });
  await page.route("**/jobs?active=true&kind=wikidata_publication_dry_run&limit=1", async (route) => {
    await route.fulfill({json: {jobs: []}});
  });
});

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
  await expect(page.getByTestId("wikidata-item-upload-actions")).toBeHidden();
  await expect(page.getByTestId("wikidata-upload-target")).toHaveCount(0);
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

test("a curator can explicitly defer connections that need new QIDs", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  const requests: unknown[] = [];
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-publications/prepare`, async (route) => {
    requests.push(route.request().postDataJSON());
    await route.fulfill({status: 200, contentType: "application/json",
      body: JSON.stringify({publication: null, operation: null})});
  });
  await gotoModernStudio(page);
  const defer = page.getByRole("checkbox", {name: "Defer connections that need new QIDs"});
  await expect(defer).not.toBeChecked();
  await defer.check();
  await expect(page.getByText(/A later Release must add the deferred connections/)).toBeVisible();
  await page.getByTestId("publication-prepare").click();
  await expect.poll(() => requests).toContainEqual(expect.objectContaining({profile_version: "1-nodes"}));
});

for (const dryOutcome of ["succeeded", "failed"] satisfies string[]) {
test(`a curator receives the asynchronous dry-run result: ${dryOutcome}`, async ({page}) => {
  test.setTimeout(75_000);
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
  let dryPolls = 0;
  let prepared = false;
  await page.route("**/wikidata-publications/latest", async (route) => {
    await route.fulfill({json: {publication: prepared ? current : null}});
  });

  await page.route("**/jobs?kind=wikidata_publication_dry_run&limit=1", async (route) => {
    await route.fulfill({json: {jobs: dryPolls >= 3 ? [{id: "dry-job", run_id: TEST_RUN_ID,
      kind: "wikidata_publication_dry_run", status: dryOutcome, params: {publication_id: "publication-7"},
      progress: {processed: 2, total: 2, message: "Check complete"}, result: {publication_id: "publication-7"},
      error: dryOutcome === "failed" ? "Wikidata read failed" : null}] : []}});
  });
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/dry-job`, async (route) => {
    dryPolls += 1;
    const done = dryPolls >= 3;
    if (done) {
      current = summary("dry_run");
      if (dryOutcome === "failed" && current.dry_run_receipt && current.plan) {
        current.dry_run_receipt.status = "failed";
        current.plan.status = "blocked";
        current.plan.action_counts = {blocked: 2};
        current.plan.blocked_actions = [{entity_key: "work-1", target_qid: "Q123", reason: "Consent required",
          consent: {entity_key: "work-1", qid: "Q123", remote_revision: 7, entity_digest: "entity-7"}},
          {entity_key: "work-2", target_qid: null, reason: "Lookup timed out", consent: null}];
      }
    }
    await route.fulfill({json: {id: "dry-job", run_id: TEST_RUN_ID, project_id: "p",
      kind: "wikidata_publication_dry_run", status: done ? dryOutcome : "running",
      params: {}, progress: {processed: done ? 2 : 1, total: 2, message: "Check Wikidata"},
      result: done ? {publication_id: "publication-7"} : null, error: done && dryOutcome === "failed" ? "Wikidata read failed" : null}});
  });

  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-publications/prepare`, async (route) => {
    prepareBody = route.request().postDataJSON();
    prepared = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({publication: current}),
    });
  });
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-publications/publication-7/read`, async (route) => {
    const body: {query?: {type?: string}} = route.request().postDataJSON();
    if (body.query?.type === "summary") {
      await route.fulfill({json: {publication: current}});
      return;
    }
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
      if (body.command.type === "dry_run") {
        await route.fulfill({json: {publication: null, operation: {
          operation_id: "dry-job", command: "dry_run", status: "queued", progress: null, error: null,
        }}});
        return;
      }
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
  if (dryOutcome === "failed") {
    await expect(page.getByRole("alert")).toContainText("Wikidata read failed", {timeout: 15000});
    await expect(page.getByTestId("publication-publish")).toBeDisabled();
    await expect(page.getByTestId("publication-dry-run")).toBeEnabled();
    expect(dryPolls).toBeGreaterThanOrEqual(3);
    await page.reload();
    await expect(page.getByRole("alert")).toContainText("Wikidata read failed", {timeout: 15000});
    await expect(page.getByTestId("publication-plan-results")).toContainText("Consent required");
    await expect(page.getByTestId("publication-job-counts")).toContainText("2 / 2");
    await expect(page.getByTestId("publication-publish")).toBeDisabled();
    const consent = page.getByRole("checkbox", {name: "I reviewed Q123 and permit this Release to update it."});
    await expect(consent).not.toBeChecked();
    await expect(page.getByRole("checkbox", {name: /I reviewed/})).toHaveCount(1);
    await expect(page.getByRole("link", {name: "Review Q123"})).toHaveAttribute("href", "https://www.wikidata.org/wiki/Q123");
    await consent.check();
    await page.getByTestId("publication-dry-run").click();
    await expect.poll(() => commands.filter((command) => command.type === "dry_run").length).toBe(2);
    expect(commands[2].foreign_qid_consents).toEqual([
      {entity_key: "work-1", qid: "Q123", remote_revision: 7, entity_digest: "entity-7"},
    ]);
    await expect(page.getByTestId("publication-dry-run")).toBeEnabled({timeout: 15000});
    await expect(consent).not.toBeChecked();
    await expect(page.getByTestId("publication-publish")).toBeDisabled();
    return;
  }
  await expect(page.getByTestId("publication-dry-run-receipt-state")).toContainText("current", {timeout: 15000});
  await expect(page.getByTestId("publication-publish")).toBeEnabled();
  expect(dryPolls).toBeGreaterThanOrEqual(3);
  await page.reload();
  await expect(page.getByTestId("publication-dry-run-receipt-state")).toContainText("current", {timeout: 15000});
  await page.getByLabel("Override cache (fresh Wikidata checks)").check();
  await page.getByTestId("publication-dry-run").click();
  await expect.poll(() => commands.filter((command) => command.type === "dry_run").length).toBe(2);
  expect(commands[2].force_refresh).toBe(true);
  await expect(page.getByTestId("publication-publish")).toBeEnabled({timeout: 15000});
  await page.getByTestId("publication-publish").click({force: true});

  await expect(page.getByTestId("publication-execution-progress")).toContainText("25 of 100,000");
  expect(commands.map((command) => command.type)).toEqual(["review", "dry_run", "dry_run", "publish"]);
  expect(commands[0]).toEqual({
    type: "review",
    release_id: RELEASE.release_id,
    expected_release_digest: RELEASE.release_digest,
    selection: {mode: "eligible_release"},
    decision: "approve",
    reason: "Curator approved the eligible Release manifest.",
  });
  expect(commands[3]).toEqual({
    type: "publish",
    plan_id: "plan-7",
    dry_run_receipt_id: "receipt-7",
    expected_receipt_digest: "sha256:receipt-7",
  });
});

}

test("the audit page reads a bounded Publication audit page", async ({page}) => {
  test.setTimeout(75_000);
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

  await expect(page.getByTestId("wikidata-publication-audit")).toContainText(
    "Execution completed with 100,000 Write Receipts.",
  );
  await expect.poll(() => auditQuery).toEqual({type: "audit", cursor: null, limit: 100});
});


test("AI report survives refresh and requires explicit approval before a fresh dry-run", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/judge-models**", (route) => route.fulfill({json: {
    default: "gemini-3.5-flash", models: [{id: "gemini-3.5-flash", label: "Gemini", available: true, supports_agentic: true}],
  }}));
  const current = summary("dry_run");
  if (!current.plan || !current.dry_run_receipt) throw new Error("Fixture requires a Plan");
  current.plan.status = "blocked";
  current.plan.action_counts = {blocked: 2};
  current.dry_run_receipt.status = "failed";
  const commands: Array<Record<string, unknown>> = [];
  let started = false;
  let polls = 0;
  const consent = {entity_key: "work-1", qid: "Q123", remote_revision: 7, entity_digest: "entity-7"};
  await page.route("**/wikidata-publications/latest", (route) => route.fulfill({json: {publication: current}}));
  await page.route("**/wikidata-publications/publication-7/read", (route) => route.fulfill({json: {
    release_id: RELEASE.release_id, release_digest: RELEASE.release_digest, items: [], total: 2, next_cursor: null,
  }}));
  await page.route("**/wikidata-publications/publication-7/ai-review", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      expect(body.plan_digest).toBe("sha256:plan-7");
      started = true;
      polls = 0;
    } else if (started) polls += 1;
    const done = polls >= 2;
    await route.fulfill({json: {
      job_id: started ? "ai-job" : null, status: started ? done ? "succeeded" : "running" : null,
      processed: done ? 2 : 0, total: 2, error: null,
      report: done ? {publication_id: "publication-7", plan_id: "plan-7", plan_digest: "sha256:plan-7",
        release_digest: RELEASE.release_digest, tier_model: "gemini-3.5-flash", created_at: "2026-09-06T10:00:00Z",
        items: [{entity_key: "work-1", label: "Supported work", qid: "Q123", status: "recommended",
          reason: "The source identifiers and claims agree.", consent},
          {entity_key: "work-2", label: "Uncertain work", qid: "Q456", status: "review_required",
            reason: "The source does not establish identity.", consent: null}]} : null,
    }});
  });
  await page.route("**/jobs/ai-job", (route) => route.fulfill({json: {
    id: "ai-job", run_id: TEST_RUN_ID, kind: "wikidata_publication_ai_review", status: "running", params: {}, progress: {},
  }}));
  await page.route("**/wikidata-publications/publication-7/advance", async (route) => {
    commands.push(route.request().postDataJSON().command);
    await route.fulfill({json: {publication: current}});
  });
  await page.addInitScript(() => localStorage.setItem("mhm.studio.reviewMode", "modern"));
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await page.getByRole("button", {name: "AI review blocked items"}).click();
  await expect(page.getByTestId("publication-ai-report")).toContainText("Supported work", {timeout: 15000});
  expect(commands).toEqual([]);
  await page.reload();
  await expect(page.getByTestId("publication-ai-report")).toContainText("The source does not establish identity.");
  await expect(page.getByRole("link", {name: "Review Q123"})).toHaveAttribute("href", "https://www.wikidata.org/wiki/Q123");
  await page.getByRole("button", {name: "Approve AI recommendations (1) and check again"}).click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0]).toMatchObject({type: "dry_run", force_refresh: true, foreign_qid_consents: [consent]});
  await expect(page.getByTestId("publication-publish")).toBeDisabled();
});

test("a curator can use an existing QID without updates in a new Release", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  const current = summary("dry_run");
  if (!current.plan || !current.dry_run_receipt) throw new Error("Fixture requires a Plan");
  current.plan.status = "blocked";
  current.plan.action_counts = {blocked: 1};
  current.dry_run_receipt.status = "failed";
  current.plan.blocked_actions = [{entity_key: "work-1", target_qid: "Q123", reason: "Consent required",
    consent: {entity_key: "work-1", qid: "Q123", remote_revision: 7, entity_digest: "entity-7"}}];
  const requests: unknown[] = [];
  await page.route("**/wikidata-publications/latest", (route) => route.fulfill({json: {publication: current}}));
  await page.route("**/wikidata-publications/publication-7/read", (route) => route.fulfill({json: {
    release_id: RELEASE.release_id, release_digest: RELEASE.release_digest, items: [], total: 0, next_cursor: null,
  }}));
  await page.route("**/wikidata-publications/prepare", (route) => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({json: {publication: summary("prepare")}});
  });
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await page.getByRole("button", {name: "Use Q123 without updates"}).click();
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({target: current.target, profile_version: current.profile_version,
    reference_only: {publication_id: "publication-7", plan_id: "plan-7", plan_digest: "sha256:plan-7", entity_keys: ["work-1"]}});
  await expect(page.getByTestId("publication-publish")).toBeDisabled();
  await expect(page.getByTestId("publication-approval-state")).toContainText("not created");
});

test("automatic resolution shows deferred items and a prepared Release without a human approval request", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/judge-models**", (route) => route.fulfill({json: {
    default: "gemini-3.5-flash", models: [{id: "gemini-3.5-flash", label: "Gemini", available: true, supports_agentic: true}],
  }}));
  const current = summary("dry_run");
  if (!current.plan || !current.dry_run_receipt) throw new Error("Fixture requires a Plan");
  current.plan.status = "blocked";
  current.plan.action_counts = {blocked: 2};
  current.dry_run_receipt.status = "failed";
  const requests: unknown[] = [];
  let ready = false;
  await page.route("**/wikidata-publications/latest", (route) => route.fulfill({json: {publication: current}}));
  await page.route("**/wikidata-publications/publication-7/read", (route) => route.fulfill({json: {
    release_id: RELEASE.release_id, release_digest: RELEASE.release_digest, items: [], total: 0, next_cursor: null,
  }}));
  await page.route("**/wikidata-publications/publication-7/ai-review", (route) => {
    if (route.request().method() === "POST") {requests.push(route.request().postDataJSON()); ready = true;}
    return route.fulfill({json: {job_id: null, status: ready ? "succeeded" : null, processed: ready ? 2 : 0, total: 2,
      error: null, report: ready ? {publication_id: "publication-7", plan_id: "plan-7", plan_digest: "sha256:plan-7",
        release_digest: RELEASE.release_digest, tier_model: "gemini-3.5-flash", created_at: "2026-09-06T12:00:00Z",
        automatic: true, policy_version: "reference-first-v1", result_publication_id: "automatic-release",
        items: [{entity_key: "work-1", label: "Existing work", qid: "Q123", status: "reuse_existing", reason: "Sources agree", consent: null},
          {entity_key: "work-2", label: "Uncertain work", qid: null, status: "deferred", reason: "No primary source", consent: null}]} : null}});
  });
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await page.getByTestId("publication-ai-review").waitFor();
  await page.getByRole("button", {name: "Resolve Release automatically"}).click({timeout: 20000});
  expect(requests[0]).toMatchObject({automatic: true, plan_digest: "sha256:plan-7"});
  await expect(page.getByTestId("publication-ai-report")).toContainText("Uncertain work · deferred");
  await expect(page.getByRole("link", {name: "Open prepared Release"})).toHaveAttribute("href", /publication=automatic-release/);
  await expect(page.getByRole("button", {name: /Approve AI recommendations/})).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("publication-ai-report")).toContainText("Uncertain work · deferred");
});
