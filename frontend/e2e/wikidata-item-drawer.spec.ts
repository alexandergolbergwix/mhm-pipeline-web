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
  test("foreign acceptance is bound to the displayed QID", async ({page}) => {
    const cases = [
      {name: "Matching", accepted: "Q111", enabled: true, checked: true},
      {name: "Stale", accepted: "Q222", enabled: true, checked: false},
      {name: "Missing", accepted: null, enabled: true, checked: false},
      {name: "Disabled", accepted: "Q111", enabled: false, checked: false},
    ];
    const items = cases.map(({name, accepted, enabled}) => ({
      ...makeStudioItem({local_id: `manuscript::${name}`, labels: {en: name}, existing_qid: "Q111"}),
      accept_foreign_modify: enabled,
      accepted_foreign_qid: accepted,
    }));
    await page.route("**/api/**", (route) => route.abort());
    await installStudioMocks(page, makeBuildResponse({items, total: items.length}));

    let patch: unknown = null;
    await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio/items/**`, (route) => {
      if (route.request().method() !== "PATCH") return route.fallback();
      patch = route.request().postDataJSON();
      Object.assign(items[1], patch);
      return route.fulfill({status: 200, contentType: "application/json", body: "{}"});
    });

    await gotoModernStudio(page);
    for (const scenario of cases) {
      await page.getByTestId(`wikidata-item-row-manuscript::${scenario.name}`).getByRole("button", {name: "Open"}).click();
      const checkbox = page.getByTestId("wikidata-accept-foreign-modify");
      await expect(page.getByText("Fetching statements and evidence…")).toBeHidden();
      await expect(checkbox).toHaveJSProperty("checked", scenario.checked);
      if (scenario.name === "Stale") {
        await checkbox.check();
        await expect.poll(() => patch).toEqual({accept_foreign_modify: true, accepted_foreign_qid: "Q111"});
        await expect(checkbox).toBeChecked();
      }
      await page.getByTestId("wikidata-item-detail-drawer").getByRole("button", {name: "Close"}).click();
    }
  });

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
    let savePayload: unknown = null;
    await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio/items/**`, (route) => {
      const url = route.request().url();
      if (route.request().method() === "PATCH") {
        savePayload = route.request().postDataJSON();
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: TEST_RUN_ID,
            local_id: "manuscript::Push Me",
            labels: {en: "Push Me"},
            descriptions: {en: "A manuscript"},
            aliases: {he: ["דחוף"]},
            add_statements: [],
            remove_statements: [],
            statement_edits: {},
          }),
        });
      }
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
    await expect(page.getByLabel("English label")).toHaveValue("Push Me");
    await expect(page.getByLabel("Hebrew label")).toHaveAttribute("dir", "rtl");
    await expect(page.getByRole("button", {name: "Save changes"})).toBeVisible();
    await page.getByRole("button", {name: "Add Hebrew alias"}).click();
    await page.getByLabel("Hebrew alias 1").fill("דחוף");
    await page.getByRole("button", {name: "Save changes"}).click();
    await expect.poll(() => savePayload).toMatchObject({aliases: {he: ["דחוף"]}});

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
