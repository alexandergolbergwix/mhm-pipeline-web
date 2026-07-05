import {expect, test} from "@playwright/test";

import {
  gotoHmoItemsTab,
  installHmoItemsMocks,
  makeHmoItemsState,
  TEST_RUN_ID,
} from "./fixtures/hmo-items-fixtures";

test.describe("HMO Wikibase Items review UI", () => {
  test.beforeEach(async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState());
  });

  test("shows item table on Wikibase Items tab", async ({page}) => {
    await gotoHmoItemsTab(page);
    await expect(page.getByTestId("hmo-items-panel")).toBeVisible();
    await expect(page.getByTestId("hmo-item-table")).toBeVisible();
    await expect(page.getByTestId("hmo-item-row-QDraft_MS_123")).toBeVisible();
  });

  test("global search filters rows", async ({page}) => {
    await gotoHmoItemsTab(page);
    await page.getByTestId("hmo-item-search").fill("missing-label");
    await expect(page.getByTestId("hmo-item-row-QDraft_MS_123")).toHaveCount(0);
    await page.getByTestId("hmo-item-search").fill("Test manuscript");
    await expect(page.getByTestId("hmo-item-row-QDraft_MS_123")).toBeVisible();
  });

  test("approval checkbox triggers override PATCH", async ({page}) => {
    let patched = false;
    await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/items/QDraft_MS_123/override`, (route) => {
      patched = true;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          run_id: TEST_RUN_ID,
          local_id: "QDraft_MS_123",
          approved: true,
          labels: {en: "Test manuscript"},
          claims: [],
          status: "would_create",
        }),
      });
    });
    await gotoHmoItemsTab(page);
    await page.getByTestId("hmo-item-approved-QDraft_MS_123").click();
    await expect.poll(() => patched).toBe(true);
  });
});
