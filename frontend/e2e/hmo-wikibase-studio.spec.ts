import {expect, test} from "@playwright/test";

import {
  gotoHmoBuildUploadTab,
  installHmoItemsMocks,
  makeHmoItemsState,
  TEST_RUN_ID,
} from "./fixtures/hmo-items-fixtures";


test.describe("HMO Wikibase Studio lifecycle", () => {
  test("renders schema counts and keeps item upload in dry-run mode by default", async ({page}) => {
    await installHmoItemsMocks(page);

    let bootstrapBody: Record<string, unknown> | null = null;
    await page.route("**/api/hmo-wikibase-schema/bootstrap", async (route) => {
      if (route.request().method() === "POST") {
        bootstrapBody = route.request().postDataJSON();
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          dry_run: true,
          created: 0,
          skipped: 30,
          failed: 0,
          would_create: 0,
          entries: [],
        }),
      });
    });

    await gotoHmoBuildUploadTab(page);

    await expect(page.getByRole("heading", {name: /10\/10 classes/})).toBeVisible();
    await expect(page.getByRole("heading", {name: /20 properties mapped/})).toBeVisible();
    await expect(page.getByTestId("hmo-upload-dry-run")).toBeChecked();

    await page.getByRole("button", {name: "Preview bootstrap"}).click();
    await expect.poll(() => bootstrapBody).toMatchObject({dry_run: true});
  });

  test("keeps item upload disabled until an item build exists", async ({page}) => {
    await installHmoItemsMocks(page, makeHmoItemsState({buildPresent: false, items: []}));
    await gotoHmoBuildUploadTab(page);

    await expect(page.getByTestId("hmo-build-items")).toBeEnabled();
    await expect(page.getByTestId("hmo-upload-submit")).toBeDisabled();
    await expect(page.getByText("Build items before uploading.")).toBeVisible();
  });

  test("posts the selected item-upload mode and preserves dry-run default", async ({page}) => {
    await installHmoItemsMocks(page);

    let uploadBody: Record<string, unknown> | null = null;
    await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/upload-items`, async (route) => {
      uploadBody = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          dry_run: true,
          created: 1,
          updated: 0,
          skipped: 0,
          failed: 0,
          blocked: 0,
          linked: 0,
          unresolved_links: 0,
          outcomes: [],
          link_outcomes: [],
        }),
      });
    });

    await gotoHmoBuildUploadTab(page);
    await page.getByTestId("hmo-upload-submit").click();

    await expect.poll(() => uploadBody).toMatchObject({
      dry_run: true,
      update_existing: false,
      allow_shacl_errors: false,
    });
    await expect(page.getByText(/Would create:/)).toBeVisible();
  });
});
