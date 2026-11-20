import {expect, test} from "@playwright/test";

import {installStudioMocks, makeBuildResponse, TEST_RUN_ID} from "./fixtures/wikidata-fixtures";

test("the administrator sees live audit progress and can inspect source gaps", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  let status = "idle";
  const report = {
    id: "audit-1", finished_at: "2026-11-20T08:17:21Z", record_count: 897, public_read: 3537,
    reference_blocks: 472, case_count: 1, requirement_coverage: {public_reads: 896},
    email_points: [{point: 8, title: "Sources, bibliography, and inscriptions", status: "manual_review"}],
  };
  await page.route("**/api/admin/wikidata-gila-repairs", route => route.fulfill({json: {
    run_id: TEST_RUN_ID, job: status === "idle" ? null : {id: "audit-1", status, progress: {
      phase: status === "running" ? "public_items" : "complete",
      processed: 200, total: 3538, message: "Read live Wikidata revisions and references.",
    }}, report: status === "succeeded" ? report : null,
  }}));
  await page.route("**/api/admin/wikidata-gila-repairs/start", route => {
    status = "running";
    return route.fulfill({json: {id: "audit-1", status}});
  });
  await page.route("**/api/admin/wikidata-gila-repairs/cases?*", route => route.fulfill({json: {
    total: 1,
    items: [{
      case_id: "source:1", qid: "Q141175895", source_value: "Weisz catalogue", points: [8],
      disposition: "manual_review", reason: "bibliographic_source_identity_unresolved",
    }],
  }}));
  await page.goto("/admin/wikidata-gila-repairs");
  await page.getByRole("button", {name: "Start live audit"}).click();
  await expect(page.getByRole("progressbar", {name: "Live audit progress"})).toBeVisible();
  await expect(page.getByText("Check Wikidata items")).toBeVisible();
  status = "succeeded";
  await page.getByRole("button", {name: /Sources, bibliography/}).click();
  await page.locator("summary").getByText("Weisz catalogue", {exact: true}).click();
  await expect(page.getByText("bibliographic source identity unresolved")).toBeVisible();
  await expect(page.getByRole("button", {name: "Prepare repair release"})).toHaveCount(0);
});

test("the remaining reference scope identifies foreign items outside the repair queue", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  await page.route("**/api/admin/wikidata-reference-repairs?scope=remaining", route => route.fulfill({json: {
    run_id: TEST_RUN_ID, total: 1, enabled: true, scope_digest: "b".repeat(64), maxlag: 100,
    job: null, verified: 0, halted_status: null,
    reference_inventory: {
      audited_at: "2026-11-20T08:17:21Z",
      items: [
        {qid: "Q141175569", baseline_blocks: 4, creator_matches: true},
        {qid: "Q134926101", baseline_blocks: 10, creator_matches: false},
      ],
    },
  }}));
  await page.goto("/admin/wikidata-reference-repairs?scope=remaining");
  await page.getByText("View the audited items").click();
  await expect(page.getByText("10 at first audit · Other creator")).toBeVisible();
  await expect(page.getByRole("button", {name: "Start backend reference repairs"})).toBeDisabled();
});
