/**
 * E2E suite for the Movement tab (provenance movement map) in the Linked
 * Data Explorer. Backend is fully mocked (Rule W-19).
 *
 * Covers: tab renders; manuscript picker populated; selecting a manuscript
 * draws the map; inferred owner appears with badge; dropped owners listed;
 * the year slider is present.
 */
import {expect, test} from "@playwright/test";
import type {Page, Route} from "@playwright/test";

const TEST_RUN_ID = "22222222-2222-2222-2222-222222222222";
const TEST_PROJECT_ID = "44444444-4444-4444-4444-444444444444";
const CN = "990000000000000001";

async function installMocks(page: Page) {
  await page.route("**/api/auth/me", (route: Route) =>
    route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        id: "33333333-3333-3333-3333-333333333333",
        email: "test@example.org", name: "Test User", role: "editor",
      }),
    }),
  );

  await page.route(`**/api/runs/${TEST_RUN_ID}`, (route: Route) =>
    route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        id: TEST_RUN_ID, project_id: TEST_PROJECT_ID, name: "Test run",
        status: "succeeded", record_count: 68, match_count: 270,
        created_at: "2026-06-06T10:02:06Z", matches: [], error: null,
      }),
    }),
  );

  await page.route(`**/api/projects/${TEST_PROJECT_ID}/research/summary`, (route) =>
    route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        total_manuscripts: 68, total_works: 69, total_persons: 223,
        total_places: 12, total_triples: 6898,
      }),
    }),
  );

  // Manuscripts picker
  await page.route(`**/api/projects/${TEST_PROJECT_ID}/research/manuscripts`, (route) =>
    route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify([
        {control_number: CN, label: "Mishneh Torah", production_year: 1500},
      ]),
    }),
  );

  // Provenance map for the manuscript
  await page.route(
    `**/api/projects/${TEST_PROJECT_ID}/research/provenance-map**`,
    (route) =>
      route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          control_number: CN,
          ms_label: "Mishneh Torah",
          stops: [
            {kind: "production", label: "Sanaa", lat: 15.35, lon: 44.2, year: 1500,
             year_earliest: 1500, year_latest: 1500, certain: true, inferred_geo: false,
             time: 1500, has_point: true},
            {kind: "owner", label: "Owner One", uri: "Q111", lat: 32.0, lon: 35.0,
             birth_year: 1480, death_year: 1540, certain: false, inferred_geo: true,
             geo_source: "P551", geo_source_label: "residence", approved: true,
             time: 1480, has_point: true},
            {kind: "current_holder", label: "National Library of Israel",
             lat: 31.79, lon: 35.2, certain: true, inferred_geo: false,
             is_present: true, time: null, has_point: true},
          ],
          edges: [
            {from: 0, to: 1, inferred: true, directed: true},
            {from: 1, to: 2, inferred: true, directed: true},
          ],
          dropped: [{label: "Anon Patron", reason: "no_location"}],
        }),
      }),
  );
}

async function setSession(page: Page) {
  await page.context().addCookies([
    {name: "session", value: "test-session", url: "http://localhost:5173"},
  ]).catch(() => {});
}

test.describe.configure({mode: "parallel"});

test.describe("Provenance Movement map", () => {
  test("Movement tab is present and selectable", async ({page}) => {
    await installMocks(page);
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/linked-data-explorer`);
    const tab = page.getByRole("button", {name: /movement/i});
    await expect(tab).toBeVisible({timeout: 8000});
    await tab.click();
    await expect(page.getByLabel(/select a manuscript/i)).toBeVisible({timeout: 8000});
  });

  test("selecting a manuscript renders the map and timeline", async ({page}) => {
    await installMocks(page);
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/linked-data-explorer`);
    await page.getByRole("button", {name: /movement/i}).click();

    await page.getByLabel(/select a manuscript/i).selectOption(CN);

    // The Leaflet map container renders.
    await expect(page.getByTestId("prov-map")).toBeVisible({timeout: 8000});
    // The sequence list shows the production place and the current holder.
    await expect(page.getByText("Sanaa")).toBeVisible();
    await expect(page.getByText("National Library of Israel")).toBeVisible();
  });

  test("inferred owner shows an 'inferred' badge", async ({page}) => {
    await installMocks(page);
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/linked-data-explorer`);
    await page.getByRole("button", {name: /movement/i}).click();
    await page.getByLabel(/select a manuscript/i).selectOption(CN);

    await expect(page.getByText("Owner One")).toBeVisible({timeout: 8000});
    await expect(page.getByText(/inferred/i).first()).toBeVisible();
  });

  test("un-geolocatable owner appears in the 'not mapped' list", async ({page}) => {
    await installMocks(page);
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/linked-data-explorer`);
    await page.getByRole("button", {name: /movement/i}).click();
    await page.getByLabel(/select a manuscript/i).selectOption(CN);

    await expect(page.getByText(/not mapped/i)).toBeVisible({timeout: 8000});
    await expect(page.getByText("Anon Patron")).toBeVisible();
  });

  test("year slider is present when stops span time", async ({page}) => {
    await installMocks(page);
    await setSession(page);
    await page.goto(`/runs/${TEST_RUN_ID}/linked-data-explorer`);
    await page.getByRole("button", {name: /movement/i}).click();
    await page.getByLabel(/select a manuscript/i).selectOption(CN);

    await expect(page.getByLabel("Year")).toBeVisible({timeout: 8000});
    await expect(page.getByRole("button", {name: /play/i})).toBeVisible();
  });
});
