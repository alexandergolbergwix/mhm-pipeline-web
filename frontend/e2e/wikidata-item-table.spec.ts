import {expect, test} from "@playwright/test";

import {
  installStudioMocks,
  makeBuildResponse,
  makeStudioItem,
  TEST_PROJECT_ID,
  TEST_RUN_ID,
} from "./fixtures/wikidata-fixtures";

async function gotoModernStudio(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    localStorage.setItem("mhm.studio.reviewMode", "modern");
  });
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await page.waitForLoadState("networkidle");
}

test.describe("Wikidata item review table", () => {
  test.beforeEach(async ({page}) => {
    await installStudioMocks(page, makeBuildResponse({
      items: [
        makeStudioItem({local_id: "manuscript::MS A", labels: {en: "MS Alpha"}}),
        makeStudioItem({
          local_id: "person::Person B",
          labels: {en: "Person Beta"},
          entity_type: "person",
          existing_qid: "Q127398",
          upload_outcome: "create",
        }),
      ],
      total: 2,
    }));
    await gotoModernStudio(page);
  });

  test("shows review table with expected columns", async ({page}) => {
    await expect(page.getByTestId("wikidata-item-table")).toBeVisible();
    await expect(page.getByTestId("wikidata-items-verify-ai")).toBeVisible();
    await expect(page.getByTestId("wikidata-items-autofix-ai")).toBeVisible();
    await expect(page.getByTestId("wikidata-item-row-manuscript::MS A")).toBeVisible();
  });

  test("data status column shows new and will update", async ({page}) => {
    await expect(page.getByTestId("wikidata-item-data-status-manuscript::MS A")).toContainText("new (not uploaded)");
    await expect(page.getByTestId("wikidata-item-data-status-person::Person B")).toContainText("will update existing");
  });

  test("global search filters rows", async ({page}) => {
    await page.getByTestId("wikidata-item-search").fill("Person Beta");
    await expect(page.getByTestId("wikidata-item-row-person::Person B")).toBeVisible();
    await expect(page.getByTestId("wikidata-item-row-manuscript::MS A")).toHaveCount(0);
  });

  test("approval checkbox triggers override PATCH", async ({page}) => {
    let patchBody: unknown = null;
    await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio/items/**`, (route) => {
      if (route.request().method() === "PATCH") {
        patchBody = route.request().postDataJSON();
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: TEST_RUN_ID,
            local_id: "manuscript::MS A",
            approved: true,
            labels: {},
            descriptions: {},
            aliases: {},
            add_statements: [],
            remove_statements: [],
            statement_edits: {},
          }),
        });
      }
      route.continue();
    });
    await gotoModernStudio(page);
    await gotoModernStudio(page);
    await page.getByTestId("wikidata-item-approved-manuscript::MS A").click();
    await expect.poll(() => patchBody).not.toBeNull();
    expect(patchBody).toMatchObject({approved: true});
  });

  test("filtering by Last upload column narrows the table", async ({page}) => {
    await installStudioMocks(page, makeBuildResponse({
      items: [
        makeStudioItem({local_id: "manuscript::A", upload_outcome: "create"}),
        makeStudioItem({local_id: "manuscript::B", upload_outcome: "failed", upload_message: "boom"}),
      ],
      total: 2,
    }));
    await gotoModernStudio(page);

    await page.getByTestId("wikidata-item-col-upload_outcome").click();
    const checkboxList = page.getByTestId("filter-mode-checkbox");
    await expect(checkboxList.locator("label", {hasText: "create"})).toContainText("(1)");
    await expect(checkboxList.locator("label", {hasText: "failed"})).toContainText("(1)");
    await checkboxList.locator("label", {hasText: "failed"}).locator('input[type="checkbox"]').check();
    await page.getByRole("button", {name: "Apply"}).click();

    await expect(page.getByTestId("wikidata-item-row-manuscript::B")).toBeVisible();
    await expect(page.getByTestId("wikidata-item-row-manuscript::A")).toHaveCount(0);
  });

  test("last-upload details show remapped claims and skipped foreign items", async ({page}) => {
    await installStudioMocks(page, makeBuildResponse({
      items: [
        makeStudioItem({
          local_id: "manuscript::Remap",
          labels: {en: "69 A 1756"},
          upload_outcome: "create",
          upload_message: "Created Q248051 (12 claims); remapped 9 properties, 3 classes (Rule W-182/W-183)",
          upload_at: "2026-08-18T12:00:00Z",
        }),
        makeStudioItem({
          local_id: "person::Foreign",
          entity_type: "person",
          labels: {en: "Community Person"},
          existing_qid: "Q209579",
          upload_outcome: "skip",
          upload_message: "Skipped Q209579 — not authored by the authenticated user (Rule-38; no UPDATE of foreign items on test.wikidata.org)",
          upload_at: "2026-08-18T12:00:01Z",
        }),
      ],
      total: 2,
    }));
    await gotoModernStudio(page);

    await page.getByTestId("wikidata-item-upload-badge-manuscript::Remap").click();
    const remapDetail = page.getByTestId("wikidata-item-upload-detail-manuscript::Remap");
    await expect(remapDetail).toBeVisible();
    await expect(remapDetail).toContainText("created");
    await expect(remapDetail).toContainText("remapped 9 properties");
    await expect(remapDetail).toContainText("Rule W-182/W-183");
    await remapDetail.getByRole("button", {name: "Close"}).click();

    await page.getByTestId("wikidata-item-upload-badge-person::Foreign").click();
    const skipDetail = page.getByTestId("wikidata-item-upload-detail-person::Foreign");
    await expect(skipDetail).toBeVisible();
    await expect(skipDetail).toContainText("skipped");
    await expect(skipDetail).toContainText("no UPDATE of foreign items");
    await expect(skipDetail).toContainText("test.wikidata.org");
  });

  test("filtering skipped upload outcomes hides created rows", async ({page}) => {
    await installStudioMocks(page, makeBuildResponse({
      items: [
        makeStudioItem({
          local_id: "manuscript::A",
          upload_outcome: "create",
          upload_message: "Created Q1 (2 claims)",
        }),
        makeStudioItem({
          local_id: "person::B",
          entity_type: "person",
          existing_qid: "Q209579",
          upload_outcome: "skip",
          upload_message: "Skipped Q209579 — not authored by the authenticated user",
        }),
      ],
      total: 2,
    }));
    await gotoModernStudio(page);

    await page.getByTestId("wikidata-item-col-upload_outcome").click();
    const checkboxList = page.getByTestId("filter-mode-checkbox");
    await checkboxList.locator("label", {hasText: "skip"}).locator('input[type="checkbox"]').check();
    await page.getByRole("button", {name: "Apply"}).click();

    await expect(page.getByTestId("wikidata-item-row-person::B")).toBeVisible();
    await expect(page.getByTestId("wikidata-item-row-manuscript::A")).toHaveCount(0);
  });

  test("data-status filter shows real row counts for shared values", async ({page}) => {
    await installStudioMocks(page, makeBuildResponse({
      items: [
        makeStudioItem({local_id: "manuscript::A"}),
        makeStudioItem({local_id: "manuscript::B"}),
        makeStudioItem({
          local_id: "person::C",
          entity_type: "person",
          existing_qid: "Q1",
        }),
      ],
      total: 3,
    }));
    await gotoModernStudio(page);
    await page.getByTestId("wikidata-item-col-data_status").click();
    const checkboxList = page.getByTestId("filter-mode-checkbox");
    await expect(checkboxList.locator("label", {hasText: "new"})).toContainText("(2)");
  });

  test("Approve all visible starts a bulk-approve job for pending filtered rows", async ({page}) => {
    let jobStartBody: Record<string, unknown> | null = null;
    await installStudioMocks(page, makeBuildResponse({
      items: [
        makeStudioItem({local_id: "manuscript::A", approved: false}),
        makeStudioItem({local_id: "manuscript::B", approved: true}),
        makeStudioItem({local_id: "person::C", approved: false, entity_type: "person"}),
      ],
      total: 3,
    }));
    await page.route(`**/api/runs/${TEST_RUN_ID}/jobs`, async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      jobStartBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "job-wd-approve-1",
          project_id: TEST_PROJECT_ID,
          run_id: TEST_RUN_ID,
          kind: "wikidata_item_bulk_approve",
          status: "succeeded",
          progress: {phase: "done", processed: 2, total: 2, message: "Done"},
          params: jobStartBody?.params ?? {},
          result: {channel: "wikidata", total: 2, approved: 2, unchanged: 0, failed: 0, cancelled: false},
          error: null,
          created_by: null,
          started_at: null,
          finished_at: null,
          cancel_requested_at: null,
          created_at: null,
          updated_at: null,
        }),
      });
    });
    page.on("dialog", (dialog) => void dialog.accept());
    await gotoModernStudio(page);
    const btn = page.getByTestId("wikidata-items-approve-visible");
    await expect(btn).toContainText("Approve all visible (2)");
    await btn.click();
    await expect.poll(() => jobStartBody).not.toBeNull();
    expect((jobStartBody as {kind?: string} | null)?.kind).toBe("wikidata_item_bulk_approve");
    const ids = ((jobStartBody as {params?: {local_ids?: string[]}} | null)?.params?.local_ids ?? []).slice().sort();
    expect(ids).toEqual(["manuscript::A", "person::C"]);
  });
});
