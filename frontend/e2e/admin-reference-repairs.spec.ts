import {expect, test} from "@playwright/test";

import {installStudioMocks, makeBuildResponse, TEST_RUN_ID} from "./fixtures/wikidata-fixtures";

const status = {
  enabled: true, scope_digest: "a".repeat(64), run_id: TEST_RUN_ID,
  total: 1602, verified: 1, halted_status: null, job: null,
};

test("an administrator confirms the exact backend repair scope", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  let started = false;
  await page.route("**/api/admin/wikidata-reference-repairs", route => route.fulfill({json: {
    ...status, job: started ? {id: "job", status: "queued", progress: {}} : null,
  }}));
  await page.route("**/api/admin/wikidata-reference-repairs/start", route => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().postDataJSON()).toEqual({confirmed: true, scope_digest: status.scope_digest});
    started = true;
    return route.fulfill({json: {id: "job", status: "queued"}});
  });
  await page.goto("/admin/wikidata-reference-repairs");
  const start = page.getByRole("button", {name: "Start backend reference repairs"});
  await expect(start).toBeDisabled();
  await page.getByRole("checkbox", {name: "I authorize the reviewed reference repairs only."}).check();
  await expect(start).toBeEnabled();
  await start.click();
  await expect(page.getByRole("button", {name: "Backend repairs are active"})).toBeDisabled();
  await expect(page.getByText("The backend runs the reviewed queue.", {exact: false})).toBeVisible();
});

test("an editor cannot mount the administrator repair page or request its data", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  let requests = 0;
  await page.route("**/api/admin/wikidata-reference-repairs**", route => {
    requests++;
    return route.abort();
  });
  await page.goto("/admin/wikidata-reference-repairs");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", {name: "Wikidata reference repairs"})).toHaveCount(0);
  expect(requests).toBe(0);
});

test("an editor cannot mount the repair inventory through a direct URL", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  let requests = 0;
  await page.route("**/wikidata-studio/repair-plan?*", route => {
    requests++;
    return route.abort();
  });
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio/repair-plan`);
  await expect(page).toHaveURL(/\/$/);
  expect(requests).toBe(0);
});
