/**
 * E2E suite for the Linked Data Explorer tab.
 *
 * Tests:
 *  1. Linked Data Explorer tile appears on the RunOverview page.
 *  2. Clicking the tile navigates to /runs/:runId/linked-data-explorer.
 *  3. The LDE page renders the provenance header and the 6 sub-tabs.
 *  4. The SPARQL console panel renders with the three source buttons.
 *
 * All backend calls are mocked — no running server needed.
 */
import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

const TEST_RUN_ID  = "22222222-2222-2222-2222-222222222222";
const TEST_PROJECT_ID = "44444444-4444-4444-4444-444444444444";

async function installMocks(page: Page) {
  // Auth
  await page.route("**/api/auth/me", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "33333333-3333-3333-3333-333333333333",
        email: "test@example.org",
        name: "Test User",
        role: "editor",
      }),
    });
  });

  // Run detail
  await page.route(`**/api/runs/${TEST_RUN_ID}`, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: TEST_RUN_ID,
        project_id: TEST_PROJECT_ID,
        name: "Test run",
        status: "succeeded",
        record_count: 68,
        match_count: 270,
        created_at: "2026-06-06T10:02:06Z",
        matches: [],
        error: null,
      }),
    });
  });

  // Extraction status (for overview tile)
  await page.route(`**/api/runs/${TEST_RUN_ID}/extraction/status`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ state: "complete", records: 68, entity_total: 248 }),
    });
  });

  // RDF status
  await page.route(`**/api/runs/${TEST_RUN_ID}/rdf/status`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "built", triples_count: 6898, manuscripts_count: 68 }),
    });
  });

  // Wikidata studio build
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio/build*`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [],
        summary: { total_items: 360, manuscripts: 68, persons: 223, works: 69, statements: 2600 },
      }),
    });
  });

  // Research summary (for ProvenanceHeader)
  await page.route(`**/api/projects/${TEST_PROJECT_ID}/research/summary`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        total_manuscripts: 68,
        total_works: 69,
        total_persons: 223,
        total_places: 12,
        total_triples: 6898,
      }),
    });
  });

  // SPARQL endpoints — return empty result for any source
  await page.route(/\/api\/projects\/[^/]+\/research\/sparql/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ columns: ["s", "p", "o"], rows: [], truncated: false }),
    });
  });
}

async function setSession(page: Page) {
  await page.context().addCookies([
    { name: "session", value: "test-session", url: "http://localhost:5173" },
  ]).catch(() => {});
}

// ── Tests ──────────────────────────────────────────────────────────────

test.describe.configure({ mode: "parallel" });

test.describe("Linked Data Explorer", () => {
  test("tile appears on RunOverview", async ({ page }) => {
    await installMocks(page);
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/overview`);

    await expect(
      page.getByRole("link", { name: /linked data explorer/i }),
    ).toBeVisible({ timeout: 8000 });
  });

  test("tile links to /runs/:id/linked-data-explorer", async ({ page }) => {
    await installMocks(page);
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/overview`);

    const tile = page.getByRole("link", { name: /linked data explorer/i });
    await expect(tile).toBeVisible({ timeout: 8000 });
    await expect(tile).toHaveAttribute("href", new RegExp(`/runs/${TEST_RUN_ID}/linked-data-explorer`));
  });

  test("LDE page renders the 6 sub-tabs", async ({ page }) => {
    await installMocks(page);
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/linked-data-explorer`);

    for (const label of ["Overview", "Clusters", "Network", "Ownership", "Geography", "SPARQL Console"]) {
      await expect(page.getByRole("button", { name: new RegExp(label, "i") }))
        .toBeVisible({ timeout: 8000 });
    }
  });

  test("SPARQL console shows three data-source buttons", async ({ page }) => {
    await installMocks(page);
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/linked-data-explorer`);

    // Navigate to SPARQL Console tab
    await page.getByRole("button", { name: /sparql console/i }).click();

    await expect(page.getByRole("button", { name: /hmo graph/i })).toBeVisible({ timeout: 8000 });
    await expect(page.getByRole("button", { name: /wikibase/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /wikidata/i })).toBeVisible();
  });

  test("executing a SPARQL template returns a results table", async ({ page }) => {
    await installMocks(page);

    // Override SPARQL mock to return one row
    await page.route(/\/api\/projects\/[^/]+\/research\/sparql/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          columns: ["work", "manuscript"],
          rows: [["urn:work:1", "urn:ms:1"]],
          truncated: false,
        }),
      });
    });

    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/linked-data-explorer`);
    await page.getByRole("button", { name: /sparql console/i }).click();

    // Click the first template button
    await page.getByRole("button", { name: /co-occurring works/i }).first().click();
    await page.getByRole("button", { name: /^run$/i }).click();

    // Table header and row should appear
    await expect(page.getByRole("columnheader", { name: /work/i })).toBeVisible({ timeout: 6000 });
    await expect(page.getByRole("cell", { name: /urn:work:1/ })).toBeVisible();
  });
});
