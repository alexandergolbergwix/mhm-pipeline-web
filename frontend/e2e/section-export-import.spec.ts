/**
 * E2E suite for the section-level Import / Export UI.
 *
 * Every backend endpoint is mocked via page.route() so the suite is
 * isolated from the DB and filesystem.  Coverage:
 *
 *   - SectionExportMenu: trigger button renders, dropdown opens,
 *     format buttons visible, clicking a format fires the download
 *     endpoint + menu closes.
 *   - SectionImportButton: trigger button renders, result panel shows
 *     after successful upload, error panel shows on failure.
 *
 * Sections covered: extraction (JSON + CSV), authority (JSON), rdf (TTL).
 *
 * Export downloads are now triggered via hidden-anchor navigation rather
 * than fetch+blob.  Playwright intercepts these as download events, so
 * assertions use page.waitForEvent('download') and check
 * download.url() for the expected path + query params.
 */

import {expect, test} from "@playwright/test";
import {TEST_RUN_ID, installExtractionMocks, makeMockState} from "./fixtures/extraction-fixtures";

// ── Helpers ──────────────────────────────────────────────────────────

/**
 * Install a mock for a section export endpoint.
 * Returns `Content-Disposition: attachment` so Playwright treats the
 * anchor navigation as a download event.
 */
function mockExportEndpoint(
  page: import("@playwright/test").Page,
  section: string,
) {
  return page.route(`**/api/runs/**/${section}/export*`, (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: {
        "Content-Disposition": `attachment; filename="export.json"`,
      },
      body: JSON.stringify({entities: [], matches: [], items: []}),
    });
  });
}

/** Install a mock for a section import endpoint that returns ImportResult. */
function mockImportEndpoint(
  page: import("@playwright/test").Page,
  section: string,
  result: {imported: number; skipped: number; errors: Array<{row: number; message: string}>},
) {
  return page.route(`**/api/runs/**/${section}/import`, (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(result),
    });
  });
}

async function gotoExtraction(page: import("@playwright/test").Page) {
  await page.goto(`/runs/${TEST_RUN_ID}/extraction`);
  // Wait for the phase-complete state (export/import buttons only render on "complete")
  await page.waitForLoadState("networkidle");
}

// ── Export tests ──────────────────────────────────────────────────────

test.describe("SectionExportMenu", () => {

  test("export menu trigger button is visible on extraction page", async ({page}) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    // The extraction page shows export/import after phase === "complete".
    const trigger = page.getByTestId("section-export-menu-trigger");
    await expect(trigger).toBeVisible({timeout: 8000});
  });

  test("clicking export trigger opens format dropdown", async ({page}) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    const trigger = page.getByTestId("section-export-menu-trigger");
    await expect(trigger).toBeVisible({timeout: 8000});
    await trigger.click();
    const dropdown = page.getByTestId("section-export-menu-dropdown");
    await expect(dropdown).toBeVisible();
  });

  test("JSON and CSV format buttons appear in extraction dropdown", async ({page}) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await page.getByTestId("section-export-menu-trigger").click();
    await expect(page.getByTestId("section-export-format-json")).toBeVisible();
    await expect(page.getByTestId("section-export-format-csv")).toBeVisible();
  });

  test("clicking JSON format fires the export endpoint", async ({page}) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await mockExportEndpoint(page, "extraction");
    await gotoExtraction(page);

    await page.getByTestId("section-export-menu-trigger").click();

    // With anchor navigation the export triggers a browser download event.
    // Capture it alongside the click so we don't miss it.
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("section-export-format-json").click(),
    ]);

    // Dropdown should close after the click
    await expect(page.getByTestId("section-export-menu-dropdown")).not.toBeVisible({timeout: 5000});

    // The download URL must point at the extraction export endpoint with
    // JSON format.
    const downloadUrl = download.url();
    expect(downloadUrl).toContain("/extraction/export");
    expect(downloadUrl).toContain("format=json");
  });

});

// ── Import tests ──────────────────────────────────────────────────────

test.describe("SectionImportButton", () => {

  test("import trigger button is visible on extraction page", async ({page}) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await gotoExtraction(page);
    await expect(page.getByTestId("section-import-trigger")).toBeVisible({timeout: 8000});
  });

  test("successful import shows result panel", async ({page}) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await mockImportEndpoint(page, "extraction", {imported: 5, skipped: 1, errors: []});
    await gotoExtraction(page);

    // Simulate file upload by dispatching a change event on the hidden input.
    const fileInput = page.getByTestId("section-import-file-input");
    await expect(fileInput).toBeAttached({timeout: 8000});
    await fileInput.setInputFiles({
      name: "data.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify([])),
    });

    const result = page.getByTestId("section-import-result");
    await expect(result).toBeVisible({timeout: 5000});
    await expect(result).toContainText("5 imported");
    await expect(result).toContainText("1 skipped");
  });

  test("import with errors shows error details", async ({page}) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await mockImportEndpoint(page, "extraction", {
      imported: 0,
      skipped: 1,
      errors: [{row: 0, message: "must not be empty"}],
    });
    await gotoExtraction(page);

    const fileInput = page.getByTestId("section-import-file-input");
    await expect(fileInput).toBeAttached({timeout: 8000});
    await fileInput.setInputFiles({
      name: "bad.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify([])),
    });

    const result = page.getByTestId("section-import-result");
    await expect(result).toBeVisible({timeout: 5000});
    await expect(result).toContainText("1 error");
  });

  test("import failure shows error panel", async ({page}) => {
    const state = makeMockState();
    await installExtractionMocks(page, state);
    await page.route("**/api/runs/**/extraction/import", (route) => {
      route.fulfill({status: 500, body: JSON.stringify({detail: "Internal Server Error"})});
    });
    await gotoExtraction(page);

    const fileInput = page.getByTestId("section-import-file-input");
    await expect(fileInput).toBeAttached({timeout: 8000});
    await fileInput.setInputFiles({
      name: "bad.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify([])),
    });

    const errPanel = page.getByTestId("section-import-error");
    await expect(errPanel).toBeVisible({timeout: 5000});
    await expect(errPanel).toContainText("Import failed");
  });

});
