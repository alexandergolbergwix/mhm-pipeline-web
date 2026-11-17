import {expect, test} from "@playwright/test";

import {installStudioMocks, makeBuildResponse, TEST_RUN_ID} from "./fixtures/wikidata-fixtures";

const plan = {
  run_id: TEST_RUN_ID,
  build_id: "build-1",
  build_token: "a".repeat(64),
  built_at: "2026-11-17T10:00:00Z",
  checked_at: "2026-11-17T11:00:00Z",
  mode: "read_only",
  public_verification: "not_checked",
  total_items: 2,
  next_cursor: "ms:a",
  items: [{
    local_id: "ms:a", label: "Manuscript A", existing_qid: "Q123",
    validation_codes: ["CATALOG_CODE_IS_QID"], qid_edit_indices: ["16"],
    approved: false, override_updated_at: null,
  }],
};

test("the repair page reads bounded pages and keeps the normal Studio path", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {id: "admin", role: "admin", name: "Admin", email: "admin@example.com"}}));
  const calls: URL[] = [];
  await page.route("**/wikidata-studio/repair-plan?*", route => {
    expect(route.request().method()).toBe("GET");
    const url = new URL(route.request().url());
    calls.push(url);
    return route.fulfill({json: url.searchParams.get("cursor")
      ? {...plan, next_cursor: null, items: [{...plan.items[0], local_id: "ms:b", label: "Manuscript B"}]}
      : plan});
  });
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio/repair-plan`);
  await expect(page.getByRole("heading", {name: "Wikidata repair plan"})).toBeVisible();
  await expect(page.getByText("CATALOG_CODE_IS_QID", {exact: true})).toBeVisible();
  await page.getByRole("button", {name: "Next page"}).click();
  await expect(page.getByText("Manuscript B", {exact: true})).toBeVisible();
  expect(calls[0].searchParams.get("page_size")).toBe("50");
  expect(calls[1].searchParams.get("cursor")).toBe("ms:a");
  expect(calls[1].searchParams.get("expected_build_token")).toBe(plan.build_token);
  await page.getByRole("button", {name: "Refresh plan"}).click();
  await expect(page.getByText("Manuscript A", {exact: true})).toBeVisible();
  expect(calls[2].searchParams.get("cursor")).toBe("");
  expect(calls[2].searchParams.has("expected_build_token")).toBe(false);
  await expect(page.getByRole("link", {name: "Open Studio"})).toHaveAttribute("href", `/runs/${TEST_RUN_ID}/wikidata-studio`);
  await expect(page.getByRole("button", {name: /publish|execute|fix all/i})).toHaveCount(0);
});

test("the repair page exposes a failed read without a false success", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {id: "admin", role: "admin", name: "Admin", email: "admin@example.com"}}));
  await page.route("**/wikidata-studio/repair-plan?*", route =>
    route.fulfill({status: 409, json: {detail: "The build changed. Refresh the repair plan."}}));
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio/repair-plan`);
  await expect(page.getByRole("alert")).toContainText("The build changed");
  await expect(page.getByRole("heading", {name: "Saved item inventory"})).toHaveCount(0);
});
