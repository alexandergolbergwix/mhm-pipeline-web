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

test.describe("Wikidata item detail drawer", () => {
  test("drawer push and apply-fix buttons capture API calls", async ({page}) => {
    const item = makeStudioItem({
      local_id: "manuscript::Push Me",
      labels: {en: "Push Me"},
      existing_qid: "Q127398",
      ai_verdict: {
        overall: "partial",
        reasoning: "Label could be clearer.",
        suggested_fixes: [{target: "label.en", value: "Better label", confidence: "high"}],
      },
    });
    await installStudioMocks(page, makeBuildResponse({items: [item], total: 1}));

    let pushCalled = false;
    let applyCalled = false;
    await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio/items/**`, (route) => {
      const url = route.request().url();
      if (route.request().method() === "POST" && url.includes("/push")) {
        pushCalled = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            local_id: "manuscript::Push Me",
            status: "updated",
            qid: "Q127398",
            message: "ok",
            test_mode: true,
          }),
        });
      }
      if (route.request().method() === "POST" && url.includes("/ai-fixes/apply")) {
        applyCalled = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: TEST_RUN_ID,
            local_id: "manuscript::Push Me",
            labels: {en: "Better label"},
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
    await page.getByTestId("wikidata-upload-submit").click();
    await expect(page.getByText("TEST MODE (test.wikidata.org)")).toBeVisible({timeout: 8000});

    await page.getByTestId("wikidata-item-row-manuscript::Push Me").getByRole("button", {name: "Open"}).click();
    await expect(page.getByTestId("wikidata-item-detail-drawer")).toBeVisible();

    await page.getByTestId("wikidata-item-apply-fix-btn").click();
    await expect.poll(() => applyCalled).toBe(true);

    await page.getByTestId("wikidata-item-push-btn").click();
    await expect.poll(() => pushCalled).toBe(true);
  });

  test("reconcile button calls per-item reconcile endpoint", async ({page}) => {
    const item = makeStudioItem({
      local_id: "person::Reconcile Me",
      labels: {en: "Reconcile Me"},
      entity_type: "person",
    });
    await installStudioMocks(page, makeBuildResponse({items: [item], total: 1}));

    let reconcileCalled = false;
    await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio/items/**`, (route) => {
      if (route.request().method() === "POST" && route.request().url().includes("/reconcile")) {
        reconcileCalled = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            local_id: "person::Reconcile Me",
            status: "adopted",
            qid: "Q555",
            message: "found",
          }),
        });
      }
      route.continue();
    });

    await gotoModernStudio(page);
    await page.getByTestId("wikidata-item-row-person::Reconcile Me").getByRole("button", {name: "Open"}).click();
    await page.getByTestId("wikidata-item-reconcile-btn").click();
    await expect.poll(() => reconcileCalled).toBe(true);
  });
});
