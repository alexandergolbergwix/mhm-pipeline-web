import {expect, test} from "@playwright/test";

import {installStudioMocks, makeBuildResponse, TEST_RUN_ID} from "./fixtures/wikidata-fixtures";

const status = {
  enabled: true, scope_digest: "a".repeat(64), run_id: TEST_RUN_ID,
  total: 1602, verified: 1, halted_status: null, job: null, maxlag: 20,
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
  await expect(page.getByRole("button", {name: "Job in queue"})).toBeDisabled();
  await expect(page.getByText("The backend runs the reviewed queue.", {exact: false})).toBeVisible();
});

test("an administrator can request recovery of a blocked repair", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  let started = false;
  await page.route("**/api/admin/wikidata-reference-repairs", route => route.fulfill({json: {
    ...status, halted_status: "blocked",
    job: started ? {id: "new-job", status: "queued", progress: {}} : {id: "old-job", status: "failed", progress: {}},
  }}));
  await page.route("**/api/admin/wikidata-reference-repairs/start", route => {
    expect(route.request().postDataJSON()).toEqual({confirmed: true, scope_digest: status.scope_digest});
    started = true;
    return route.fulfill({json: {id: "new-job", status: "queued"}});
  });
  await page.goto("/admin/wikidata-reference-repairs");
  const resume = page.getByRole("button", {name: "Check previous repair and resume"});
  await expect(resume).toBeDisabled();
  await page.getByRole("checkbox", {name: "I authorize the reviewed reference repairs only."}).check();
  await resume.click();
  await expect(page.getByRole("button", {name: "Job in queue"})).toBeDisabled();
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

test("live progress follows backend states without another start request", async ({page}) => {
  await installStudioMocks(page, makeBuildResponse());
  await page.route("**/api/auth/me", route => route.fulfill({json: {
    id: "admin", role: "admin", name: "Admin", email: "admin@example.com",
  }}));
  await page.route("**/api/admin/stats", route => route.fulfill({json: {pending_access_requests: 0}}));
  let phase = "queued";
  let verified = 1;
  let starts = 0;
  await page.route("**/api/admin/wikidata-reference-repairs/start", route => {
    starts++;
    return route.abort();
  });
  await page.route("**/api/admin/wikidata-reference-repairs", route => route.fulfill({json: {
    ...status, verified, completed: verified, already_clean: 0,
    job: {id: "job", status: phase === "queued" ? "queued" : "running", progress: {
      phase, current_qid: "Q123", next_attempt_at: new Date(Date.now() + 60000).toISOString(),
      lag_probe_status: "delayed", next_lag_probe_at: new Date(Date.now() + 60000).toISOString(),
      mirror_scan: {status: "complete", reviewed: 1601, total: 1601,
        candidates: 18, seen_clean: 250, unchecked: 1333},

    }},
  }}));
  await page.goto("/admin/wikidata-reference-repairs");
  const progress = page.getByRole("progressbar", {name: "Confirmed reference repair progress"});
  await expect(page.getByRole("heading", {name: "In queue", exact: true})).toBeVisible();
  await expect(progress).toHaveAttribute("value", "1");
  phase = "service_delay";
  await expect(page.getByRole("heading", {name: "Wikidata delay", exact: true})).toBeVisible();
  await expect(page.getByText(/Next retry in/)).toBeVisible();
  await expect(page.getByText(/Wikidata still requests a delay/)).toBeVisible();
  await expect(page.getByText(/18 possible repairs · 250 appear clean · 1333 unavailable/))
    .toBeVisible();
  await expect(progress).toHaveAttribute("value", "1");
  verified = 2;
  phase = "active";
  await expect(progress).toHaveAttribute("value", "2");
  await expect(page.getByRole("heading", {name: "Repairs in progress", exact: true})).toBeVisible();
  expect(starts).toBe(0);
});
