import {expect, test} from "@playwright/test";

const localViewer = process.env.GILA_LOCAL_UI_URL;
test.skip(!localViewer, "Set GILA_LOCAL_UI_URL for the separate local operator UI.");

const snapshot = {
  active: false, status: "paused", verified: 1, already_clean: 0, total: 1602,
  current_qid: "Q123", next_attempt_at: "2026-11-17T18:00:00Z",
  scope_digest: "reviewed-test-scope", creator_uid: 6935067, maxlag: 5,
};

test("the operator confirms the reference scope before one start request", async ({page}) => {
  const posts: unknown[] = [];
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/repairs/status") return route.fulfill({json: snapshot});
    if (path === "/api/repairs/start") {
      expect(route.request().method()).toBe("POST");
      posts.push(route.request().postDataJSON());
      return route.fulfill({json: {...snapshot, active: true, status: "start_requested"}});
    }
    return route.abort();
  });
  await page.goto(localViewer ?? "/");
  const button = page.getByRole("button", {name: "Start reference repairs", exact: true});
  await expect(button).toBeDisabled();
  await expect(page.locator("#repair-status")).toContainText("paused");
  await page.getByRole("checkbox", {name: "I authorize the reviewed reference repairs only."}).check();
  await expect(button).toBeEnabled();
  await button.click();
  await expect(page.getByRole("button", {name: "Reference repairs are active"})).toBeDisabled();
  expect(posts).toEqual([{confirmed: true, scope_digest: snapshot.scope_digest}]);
});

test("an active queue exposes its retry time and blocks another start", async ({page}) => {
  let starts = 0;
  await page.route("**/api/**", route => {
    if (route.request().method() === "POST") starts++;
    return route.fulfill({json: {...snapshot, active: true, status: "service_delay"}});
  });
  await page.goto(localViewer ?? "/");
  await expect(page.getByRole("button", {name: "Reference repairs are active"})).toBeDisabled();
  await expect(page.locator("#repair-status")).toContainText(snapshot.next_attempt_at);
  await page.getByRole("checkbox", {name: "I authorize the reviewed reference repairs only."}).check();
  await expect(page.getByRole("button", {name: "Reference repairs are active"})).toBeDisabled();
  expect(starts).toBe(0);
});

test("a refused start does not cause an automatic resend", async ({page}) => {
  let starts = 0;
  await page.route("**/api/**", route => {
    if (route.request().method() === "POST") {
      starts++;
      return route.fulfill({status: 409, json: {error: "The scope changed."}});
    }
    return route.fulfill({json: snapshot});
  });
  await page.goto(localViewer ?? "/");
  await expect(page.locator("#repair-status")).toContainText("paused");
  await page.getByRole("checkbox", {name: "I authorize the reviewed reference repairs only."}).check();
  await page.getByRole("button", {name: "Start reference repairs", exact: true}).click();
  await expect(page.locator("#repair-status")).toContainText("No automatic resend");
  await page.waitForTimeout(5500);
  expect(starts).toBe(1);
});
