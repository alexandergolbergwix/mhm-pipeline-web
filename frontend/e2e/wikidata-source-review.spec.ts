import {expect, test} from "@playwright/test";

import {installStudioMocks, makeBuildResponse, makeStudioItem, TEST_RUN_ID} from "./fixtures/wikidata-fixtures";

test("the drawer saves one source occurrence without a public write", async ({page}) => {
  await page.route(url => url.pathname.startsWith("/api/"), route => route.abort());
  const item = makeStudioItem({local_id: "manuscript::Source", labels: {en: "Source"}, approved: false});
  await installStudioMocks(page, makeBuildResponse({items: [item], total: 1}));
  let request: unknown = null;
  let publicWrites = 0;
  await page.route("https://www.wikidata.org/**", route => {
    if (route.request().method() === "POST") publicWrites += 1;
    return route.abort();
  });
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio/source-review**`, route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({local_id: item.local_id, records: [{
      control_number: "990001", source_hash: "a".repeat(64), original_occurrences_available: true,
      requirements: [{point: 3, title: "Work identities and links", status: "identity_review"}],
      counts: {identity_review: 1}, source_gaps: [], record_status_notes: ["רשומה זמנית"], catalog_references: [],
      stale_choices: [], passages: [], candidates: [{kind: "work_occurrence", title: "עץ חיים", source_field: "505",
        source_key: "505:1", source_text: "עץ חיים, 21ב–22ב", folio: "21ב–22ב", author: "חיים ויטאל",
        control_number: "990001", source_hash: "a".repeat(64), candidate_key: "b".repeat(64), status: "identity_review"}],
    }]}),
  }));
  await page.route(`**/api/runs/${TEST_RUN_ID}/wikidata-studio/source-choice`, route => {
    request = route.request().postDataJSON();
    return route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify({rebuild_required: true})});
  });
  await page.addInitScript(() => localStorage.setItem("mhm.studio.reviewMode", "modern"));
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await page.getByTestId("wikidata-item-row-manuscript::Source").getByRole("button", {name: "Open"}).click();
  const review = page.getByRole("region", {name: "Gila source review"});
  await expect(review.getByText("Catalogue status: רשומה זמנית")).toBeVisible();
  await review.locator("summary").click();
  await expect(review.getByText("Source folio: 21ב–22ב")).toBeVisible();
  await review.getByLabel("Existing target QID").fill("Q123");
  await review.getByLabel("Evidence URL").fill("https://www.nli.org.il/test");
  await review.getByLabel("Reason or unresolved fact").fill("The source and target agree.");
  await review.getByRole("button", {name: "Save local choice"}).click();
  await expect.poll(() => request).toMatchObject({local_id: item.local_id, control_number: "990001",
    candidate_key: "b".repeat(64), expected_source_hash: "a".repeat(64), target_qid: "Q123", decision: "select"});
  expect(request).not.toHaveProperty("approved");
  expect(publicWrites).toBe(0);
});
