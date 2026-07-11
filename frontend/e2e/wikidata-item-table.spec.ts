import {expect, test} from "@playwright/test";

import {
  installStudioMocks,
  makeBuildResponse,
  makeStudioItem,
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
    await checkboxList.locator("label", {hasText: "failed"}).locator('input[type="checkbox"]').check();
    await page.getByRole("button", {name: "Apply"}).click();

    await expect(page.getByTestId("wikidata-item-row-manuscript::B")).toBeVisible();
    await expect(page.getByTestId("wikidata-item-row-manuscript::A")).toHaveCount(0);
  });
});
