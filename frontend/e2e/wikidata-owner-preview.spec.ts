import {expect, test} from "@playwright/test";

import {installStudioMocks, makeBuildResponse, TEST_PROJECT_ID, TEST_RUN_ID} from "./fixtures/wikidata-fixtures";

test.beforeEach(async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/wikidata-publications/latest", (route) => route.fulfill({json: {publication: null}}));
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs?kind=wikidata_upload&limit=100`, (route) => route.fulfill({json: {jobs: []}}));
  await page.addInitScript(() => localStorage.setItem("mhm.studio.reviewMode", "modern"));
});

test("restores the current user's completed preview and ignores other creators", async ({page}) => {
  const params = {owner_only: true, dry_run: true, upload_target: "dry_run", update_existing: true, source: "canonical"};
  const saved = {
    id: "saved-owner-preview", project_id: TEST_PROJECT_ID, run_id: TEST_RUN_ID, kind: "wikidata_upload",
    status: "succeeded", params, created_by: "user-1", progress: {}, error: null,
    result: {outcomes: [{local_id: "own", label: "Own item", entity_type: "work", qid: "Q1", status: "blocked", message: "Saved reason"}], link_outcomes: []},
  };
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs?kind=wikidata_upload&limit=100`, (route) => route.fulfill({json: {jobs: [
    {...saved, id: "foreign-preview", created_by: "user-2", result: {outcomes: [], link_outcomes: []}},
    {...saved, id: "unknown-preview", created_by: null, result: {outcomes: [], link_outcomes: []}},
    saved,
  ]}}));
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await expect(page.getByTestId("wikidata-owner-preview-table")).toContainText("Saved reason");
  await page.reload();
  await expect(page.getByTestId("wikidata-owner-preview-table")).toContainText("Saved reason");
});

test("does not attach an active preview from another or unknown creator", async ({page}) => {
  const params = {owner_only: true, dry_run: true, upload_target: "dry_run", update_existing: true};
  const foreign = {id: "foreign-preview", project_id: TEST_PROJECT_ID, run_id: TEST_RUN_ID,
    kind: "wikidata_upload", status: "running", params, created_by: "user-2", progress: {}, result: null, error: null};
  let polls = 0;
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs?kind=wikidata_upload&limit=100`, (route) => route.fulfill({json: {jobs: [foreign, {...foreign, id: "unknown", created_by: null}]}}));
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs?active=true`, (route) => route.fulfill({json: {jobs: [foreign]}}));
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/foreign-preview`, async (route) => { polls++; await route.fulfill({json: foreign}); });
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  const preview = page.getByTestId("wikidata-owner-preview");
  await expect(preview.getByTestId("wikidata-owner-preview-start")).toBeEnabled();
  await expect(preview).not.toContainText("Preview status: running");
  expect(polls).toBe(0);
});

test("shows cancellation failures for the preview", async ({page}) => {
  const params = {owner_only: true, dry_run: true, upload_target: "dry_run", update_existing: true};
  const active = {id: "own-preview", project_id: TEST_PROJECT_ID, run_id: TEST_RUN_ID, created_by: "user-1",
    kind: "wikidata_upload", status: "running", params, progress: {}, result: null, error: null};
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs?kind=wikidata_upload&limit=100`, (route) => route.fulfill({json: {jobs: [active]}}));
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/own-preview`, (route) => route.fulfill({json: active}));
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/own-preview/cancel`, (route) => route.fulfill({status: 403, json: {detail: "Cancellation denied"}}));
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  const preview = page.getByTestId("wikidata-owner-preview");
  await preview.getByRole("button", {name: "Cancel preview"}).click();
  await expect(preview.getByRole("alert")).toContainText("Cancellation denied");
});

test("shows cancellation failures in the preview progress modal", async ({page}) => {
  const params = {owner_only: true, dry_run: true, upload_target: "dry_run", update_existing: true};
  const active = {id: "modal-preview", project_id: TEST_PROJECT_ID, run_id: TEST_RUN_ID, created_by: "user-1",
    kind: "wikidata_upload", status: "running", params, progress: {}, result: null, error: null};
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs?kind=wikidata_upload&limit=100`, (route) => route.fulfill({json: {jobs: [active]}}));
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/modal-preview`, (route) => route.fulfill({json: active}));
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/modal-preview/cancel`, (route) => route.fulfill({status: 403, json: {detail: "Cancellation denied"}}));
  page.on("dialog", (dialog) => void dialog.accept());
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio?job=modal-preview`);
  const modal = page.getByTestId("wikidata-upload-progress-modal");
  await modal.getByTestId("wikidata-upload-modal-cancel").click();
  await expect(modal.getByRole("alert")).toContainText("Cancellation denied");
});

test("does not attach an owner preview after a normal upload conflict", async ({page}) => {
  const foreign = {id: "owner-active", project_id: TEST_PROJECT_ID, run_id: TEST_RUN_ID, created_by: "user-2",
    kind: "wikidata_upload", status: "running", params: {owner_only: true, dry_run: true}, progress: {}, result: null, error: null};
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-publications/prepare`, (route) => route.fulfill({status: 410, json: {detail: "Unavailable"}}));
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs?active=true`, (route) => route.fulfill({json: {jobs: [foreign]}}));
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({status: 409, json: {detail: {message: "job already running", job_id: foreign.id}}});
  });
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await page.getByTestId("publication-prepare").click();
  const panel = page.getByTestId("wikidata-item-upload-actions");
  await panel.getByTestId("wikidata-upload-submit").click();
  await expect(panel).toContainText("Another Wikidata upload job is active");
  await expect(panel.getByTestId("wikidata-upload-submit")).toBeEnabled();
  await expect(page.getByTestId("wikidata-upload-progress-modal")).toHaveCount(0);
});

test("previews owner eligibility and connections without changing upload status", async ({page}) => {
  const requests: unknown[] = [];
  const params = {
    owner_only: true, upload_target: "dry_run", dry_run: true, update_existing: true,
    source: "canonical", approved_only: true, item_approved_only: false,
  };
  const result = {
    dry_run: true, upload_target: "dry_run",
    outcomes: [
      {local_id: "person::Moses Maimonides", label: "Moses Maimonides", entity_type: "person", qid: "Q127398", status: "would_update", message: "Eligible to attempt update", added_properties: []},
      {local_id: "manuscript::Hebrew Manuscript", label: "Hebrew Manuscript", entity_type: "manuscript", qid: null, status: "blocked", message: "Target or creator could not be verified", added_properties: []},
    ],
    link_outcomes: [{source_local_id: "person::Moses Maimonides", property_id: "P50", target_local_id: "work:1", status: "unresolved", message: "Target was not created", qid: "Q127398"}],
  };
  const snapshot = (status: string) => ({
    id: "owner-preview", project_id: TEST_PROJECT_ID, run_id: TEST_RUN_ID,
    kind: "wikidata_upload", status, params, created_by: "user-1",
    progress: {phase: status, item_outcome: {local_id: "person::Moses Maimonides", status: "processing"}},
    result: status === "succeeded" ? result : null, error: null,
  });
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    requests.push(route.request().postDataJSON());
    await route.fulfill({json: snapshot("running")});
  });
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/owner-preview`, (route) => route.fulfill({json: snapshot("succeeded")}));
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await expect(page.getByTestId("publication-prepare")).toBeVisible();
  const preview = page.getByTestId("wikidata-owner-preview");
  await expect(preview).toContainText("no live writes");
  await expect(preview).toContainText("does not record upload status");
  await preview.getByTestId("wikidata-owner-preview-start").click();
  await expect.poll(() => requests).toEqual([{kind: "wikidata_upload", params}]);
  await expect(preview.getByTestId("wikidata-owner-preview-counts")).toContainText("Eligible to attempt update: 1 | Blocked: 1");
  await expect(preview.getByTestId("wikidata-owner-preview-table")).toContainText("Target or creator could not be verified");
  await expect(preview.getByTestId("wikidata-owner-preview-links")).toContainText("Target was not created");
  await expect(page.getByTestId("wikidata-item-upload-outcome-person::Moses Maimonides")).toContainText("never tried");
  await expect(page.getByTestId("wikidata-upload-target")).toHaveCount(0);
});

test("does not patch the review table when the compatibility upload panel is visible", async ({page}) => {
  const params = {owner_only: true, upload_target: "dry_run", dry_run: true, update_existing: true};
  const snapshot = {
    id: "owner-active", project_id: TEST_PROJECT_ID, run_id: TEST_RUN_ID,
    kind: "wikidata_upload", status: "running", params, result: null, error: null, created_by: "user-1",
    progress: {phase: "uploading", item_outcome: {local_id: "person::Moses Maimonides", status: "processing"}},
  };
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-publications/prepare`, (route) => route.fulfill({status: 410, json: {detail: "Unavailable"}}));
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/owner-active`, (route) => route.fulfill({json: snapshot}));
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({json: snapshot});
  });
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await page.getByTestId("publication-prepare").click();
  await expect(page.getByTestId("wikidata-item-upload-actions")).toBeVisible();
  await page.getByTestId("wikidata-owner-preview-start").click();
  await expect(page.getByTestId("wikidata-owner-preview")).toContainText("Preview status: running");
  await expect(page.getByTestId("wikidata-item-upload-outcome-person::Moses Maimonides")).toContainText("never tried");
  await expect(page.getByTestId("wikidata-upload-submit")).toBeDisabled();
});

test("shows a missing live credential error without starting another job", async ({page}) => {
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({status: 400, json: {detail: "Live upload requires a Wikidata (live) bot password in Settings."}});
  });
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  const preview = page.getByTestId("wikidata-owner-preview");
  await preview.getByTestId("wikidata-owner-preview-start").click();
  await expect(preview.getByRole("alert")).toContainText("bot password in Settings");
  await expect(preview.getByTestId("wikidata-owner-preview-start")).toBeEnabled();
});

test("does not attach a start response with an unknown creator", async ({page}) => {
  const params = {owner_only: true, upload_target: "dry_run", dry_run: true, update_existing: true};
  let polls = 0;
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({json: {id: "unknown", run_id: TEST_RUN_ID, project_id: TEST_PROJECT_ID,
      kind: "wikidata_upload", status: "running", params, created_by: null, progress: {}, result: null, error: null}});
  });
  await page.route(`**/api/runs/${TEST_RUN_ID}/jobs/unknown`, async (route) => { polls++; await route.fulfill({status: 500}); });
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  const preview = page.getByTestId("wikidata-owner-preview");
  await preview.getByTestId("wikidata-owner-preview-start").click();
  await expect(preview.getByRole("alert")).toContainText("could not be verified");
  expect(polls).toBe(0);
});
