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

    await page.getByText("Advanced: catalogue schema maintenance").click();
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
    await expect(page.getByTestId("hmo-item-upload-actions").getByText("Would create:")).toBeVisible();
  });

  test("coverage row hover title and click popover explain projection", async ({page}) => {
    await installHmoItemsMocks(page);
    await gotoHmoBuildUploadTab(page);

    await page.getByTestId("hmo-studio-tab-coverage").click();
    const row = page.getByTestId("hmo-coverage-row-Codicological_Unit");
    await expect(row).toBeVisible();
    await expect(row).toHaveAttribute("title", /CU nodes remain in HMO/);
    await row.click();
    const detail = page.getByTestId("hmo-coverage-detail-Codicological_Unit");
    await expect(detail).toBeVisible();
    await expect(detail.getByText("How Wikidata represents this")).toBeVisible();
    await expect(detail.getByText(/Manuscript-level part count/)).toBeVisible();
    await expect(detail.getByText(/CU nodes remain in HMO/)).toBeVisible();
    await expect(detail.getByRole("link", {name: "P2635"})).toBeVisible();
  });

  test("studio sub-tabs default to items and switch to coverage/rdf/manifests", async ({page}) => {
    await installHmoItemsMocks(page);

    await page.unroute(`**/api/runs/${TEST_RUN_ID}/hmo-studio/status`);
    await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/status`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          state: "built",
          rdf_present: true,
          wikibase_configured: true,
          coverage_present: true,
          manifest_count: 1,
          last_upload_at: null,
          last_upload: null,
          canonical_live_count: 0,
          canonical_ready: false,
        }),
      });
    });

    await page.unroute(`**/api/runs/${TEST_RUN_ID}/hmo-studio/manifests`);
    await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/manifests`, async (route) => {
      const url = route.request().url();
      if (/\/hmo-studio\/manifests\/?(\?|$)/.test(url)) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            manifest_count: 1,
            manifest_dir: "/tmp",
            manifests: [{
              shelfmark: "Heb.8",
              file: "MS_Heb.8.json",
              canvas_count: 2,
              range_count: 1,
              annotation_count: 0,
              seealso_count: 1,
            }],
          }),
        });
        return;
      }
      await route.continue();
    });
    await page.route(`**/api/runs/${TEST_RUN_ID}/hmo-studio/manifests/Heb.8`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({type: "Manifest", id: "https://example.org/manifest"}),
      });
    });
    await page.route(`**/api/runs/${TEST_RUN_ID}/rdf/**`, async (route) => {
      const url = route.request().url();
      if (url.includes("/status")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({status: "idle"}),
        });
        return;
      }
      await route.fulfill({status: 200, contentType: "application/json", body: "{}"});
    });

    await gotoHmoBuildUploadTab(page);
    await expect(page.getByTestId("hmo-studio-panel-items")).toBeVisible();
    await expect(page.getByTestId("hmo-studio-panel-coverage")).toHaveCount(0);

    await page.getByTestId("hmo-studio-tab-coverage").click();
    await expect(page.getByTestId("hmo-studio-panel-coverage")).toBeVisible();

    await page.getByTestId("hmo-studio-tab-rdf").click();
    await expect(page.getByTestId("hmo-studio-panel-rdf")).toBeVisible();
    await expect(page.getByTestId("hmo-rdf-explorer")).toBeVisible();

    await page.getByTestId("hmo-studio-tab-manifests").click();
    await expect(page.getByTestId("hmo-studio-panel-manifests")).toBeVisible();
    await expect(page.getByTestId("hmo-manifest-row-Heb_8")).toBeVisible();
    await page.getByRole("cell", {name: "Heb.8"}).click({force: true});
    await expect(page.getByTestId("hmo-manifest-preview")).toContainText('"type": "Manifest"', {
      timeout: 15_000,
    });
  });
});
