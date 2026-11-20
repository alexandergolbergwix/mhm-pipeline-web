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
    return route.fulfill({json: {id: "audit-1", status, progress: {phase: "queued"}, error: null}});
  });
  await page.route("**/api/admin/wikidata-gila-repairs/cases?*", route => route.fulfill({json: {
    total: 1,
    items: [{
      case_id: "source:1", qid: "Q141175895", source_value: "Weisz catalogue", points: [8],
      disposition: "manual_review", reason: "bibliographic_source_identity_unresolved",
    }],
  }}));
  await page.goto("/admin/wikidata-gila-repairs");
  await expect(page.getByRole("heading", {name: "Wikidata Manuscript Audit and Repair"})).toBeVisible();
  await page.getByText("How repairs work", {exact: true}).click();
  await expect(page.getByText("Run a dry run before you publish.", {exact: false})).toBeVisible();
  await page.getByRole("button", {name: "Start live audit"}).click();
  await expect(page.getByRole("progressbar", {name: "Live audit progress"})).toBeVisible();
  await expect(page.getByText("Check Wikidata items")).toBeVisible();
  status = "succeeded";
  await page.getByRole("button", {name: /Sources, bibliography/}).click();
  await page.locator("summary").getByText("Weisz catalogue", {exact: true}).click();
  await expect(page.getByText("bibliographic source identity unresolved")).toBeVisible();
  await expect(page.getByRole("button", {name: "Prepare repair release"})).toHaveCount(0);
});

test("the administrator can rebuild the source and retry an audit that stopped", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  const previousAudit = {
    id: "audit-1", status: "failed", progress: {phase: "check_source"},
    error: "Build the canonical Studio source and start the audit again.",
  };
  const nextAudit = {
    id: "audit-2", status: "running", progress: {phase: "public_items"},
    error: null,
  };
  let audit: typeof previousAudit | typeof nextAudit = previousAudit;
  await page.route("**/api/admin/wikidata-gila-repairs", route =>
    route.fulfill({json: {run_id: TEST_RUN_ID, job: audit, report: null}}));

  const params = {
    source: "canonical", approved_only: true, force_rebuild: true, extract_provenance_prose: false,
  };
  const build = {id: "source-build", run_id: TEST_RUN_ID, kind: "wikidata_studio_build", params};
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, route => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().postDataJSON()).toEqual({kind: build.kind, params});
    return route.fulfill({json: {...build, status: "queued", progress: {phase: "queued"}}});
  });
  let completeBuild = false;
  let checkedSuccessfulBuild = false;
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/${build.id}`, route => {
    checkedSuccessfulBuild = completeBuild;
    return route.fulfill({json: {
      ...build, status: completeBuild ? "succeeded" : "running",
      progress: {message: "Read the approved MARC records."},
    }});
  });
  await page.route("**/api/admin/wikidata-gila-repairs/start", route => {
    expect(checkedSuccessfulBuild).toBe(true);
    audit = nextAudit;
    return route.fulfill({json: nextAudit});
  });
  await page.goto("/admin/wikidata-gila-repairs");

  await page.getByRole("button", {name: "Rebuild source and run audit"}).click({timeout: 5000});
  await expect(page.getByRole("status").getByText("Read the approved MARC records.")).toBeVisible();
  completeBuild = true;
  await expect(page.getByText("Check Wikidata items")).toBeVisible();
});

test("the audit shows work left and opens the scoped repair controls", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  const finishedAt = "2026-11-20T08:17:21Z";
  await page.route("**/api/admin/wikidata-gila-repairs", route => route.fulfill({json: {
    run_id: TEST_RUN_ID, job: {id: "audit-1", status: "succeeded", progress: {phase: "complete"}},
    report: {
      id: "audit-1", finished_at: finishedAt, record_count: 897, public_read: 2161,
      reference_blocks: 14, case_count: 2, all_requirements_verified: false,
      requirement_coverage: {public_reads: 896, verified_claims: 8399},
      email_points: [{point: 8, title: "Sources, bibliography, and inscriptions", status: "manual_review"}],
    },
  }}));
  await page.route("**/api/admin/wikidata-gila-repairs/cases?*", route =>
    route.fulfill({json: {total: 0, items: []}}));
  const scopeDigest = "b".repeat(64);
  let starts = 0;
  const repairJob = {id: "reference-job", status: "queued", progress: {}};
  await page.route("**/api/admin/wikidata-reference-repairs?scope=remaining", route => route.fulfill({json: {
    run_id: TEST_RUN_ID, total: 1, enabled: true, scope_digest: scopeDigest, maxlag: 100,
    job: starts > 0 ? repairJob : null, verified: 0, halted_status: null,
    reference_inventory: {
      audited_at: finishedAt, live_checked_at: finishedAt,
      items: [
        {qid: "Q141175569", baseline_blocks: 4, observed_blocks: 4, creator_matches: true},
        {qid: "Q134926101", baseline_blocks: 10, observed_blocks: 10, creator_matches: false},
      ],
    },
  }}));
  await page.route("**/api/admin/wikidata-reference-repairs/start", route => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().postDataJSON()).toEqual({
      confirmed: true, scope_digest: scopeDigest, scope: "remaining",
    });
    starts++;
    return route.fulfill({json: repairJob});
  });
  await page.goto("/admin/wikidata-gila-repairs");
  await expect(page.getByRole("heading", {name: "Work remains after the audit"})).toBeVisible();
  await expect(page.getByText("0 of 9", {exact: true})).toBeVisible();
  await expect(page.getByText("4 repeated references on project-created items.", {exact: false})).toBeVisible();
  await expect(page.getByText("10 on items that other accounts created.", {exact: false})).toBeVisible();
  await page.getByRole("link", {name: "Start fixing references"}).click();
  await expect(page).toHaveURL(/wikidata-reference-repairs\?scope=remaining$/);
  await page.getByText("View the audited items").click();
  await expect(page.getByText("10 at latest audit · Other creator")).toBeVisible();
  const start = page.getByRole("button", {name: "Start backend reference repairs"});
  await expect(start).toBeDisabled();
  expect(starts).toBe(0);
  await page.getByRole("checkbox", {name: "I authorize the reviewed reference repairs only."}).check();
  await start.click();
  await expect(page.getByRole("heading", {name: "In queue", exact: true})).toBeVisible();
  expect(starts).toBe(1);
});

test("the audit asks for verification after reference repairs and exports evidence", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  const report = {
    id: "audit-1", finished_at: "2026-11-20T08:17:21Z", record_count: 897, public_read: 2161,
    reference_blocks: 472, case_count: 1, all_requirements_verified: false,
    requirement_coverage: {public_reads: 896, verified_claims: 8399},
    email_points: [{point: 8, title: "Sources, bibliography, and inscriptions", status: "manual_review"}],
  };
  let audit = {id: "audit-1", status: "succeeded", progress: {phase: "complete"}};
  await page.route("**/api/admin/wikidata-gila-repairs", route =>
    route.fulfill({json: {run_id: TEST_RUN_ID, job: audit, report}}));
  await page.route("**/api/admin/wikidata-gila-repairs/start", route => {
    audit = {id: "audit-2", status: "running", progress: {phase: "public_items"}};
    return route.fulfill({json: audit});
  });
  await page.route("**/api/admin/wikidata-gila-repairs/cases?*", route =>
    route.fulfill({json: {items: [], total: 0}}));
  await page.route("**/api/admin/wikidata-gila-repairs/evidence?audit_id=audit-1", route =>
    route.fulfill({json: {audit_id: "audit-1", reference_repairs: [{qid: "Q42", after_revision: 22}]}}));
  await page.route("**/api/admin/wikidata-reference-repairs?scope=remaining", route =>
    route.fulfill({json: {
      enabled: true, verified: 9,
      job: {id: "repairs", status: "succeeded", finished_at: "2026-11-20T09:15:00Z"},
    }}));
  await page.goto("/admin/wikidata-gila-repairs");
  await expect(page.getByRole("heading", {name: "Reference repairs complete. A fresh audit is due."})).toBeVisible();
  await expect(page.getByText("Repeated references at last audit")).toBeVisible();
  await page.getByRole("combobox", {name: "Case status"}).selectOption("ready_to_prepare");
  await expect(page.getByText("No source-supported cases are ready", {exact: false})).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", {name: "Download audit evidence"}).click();
  expect((await download).suggestedFilename()).toBe("wikidata-audit-audit-1.json");
  await page.getByRole("button", {name: "Verify repairs on Wikidata"}).click();
  await expect(page.getByText("Check Wikidata items")).toBeVisible();
});
