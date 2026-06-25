/**
 * E2E — Authority table duplicate grouping + notes search (Rule W-31 / W-33).
 */
import {expect, test} from "@playwright/test";
import {
  makeAuthorityState,
  installAuthorityMocks,
  gotoAuthority,
} from "./fixtures/authority-fixtures";

test.describe("Authority review — grouping and notes search", () => {

  test("group duplicates collapses same entity with different roles", async ({page}) => {
    const state = makeAuthorityState();
    await installAuthorityMocks(page, state);
    await gotoAuthority(page);

    await expect(page.getByTestId(/^authority-row-/).first()).toBeVisible({timeout: 8000});

    // Default: group on → 2 visible primary rows (dup + Jerusalem)
    await expect(page.getByTestId(/^authority-row-/)).toHaveCount(2);

    const expand = page.getByTestId("expand-group-MS-001__אלוני נחמיה");
    await expect(expand).toBeVisible();
    await expand.click();
    await expect(page.getByTestId("authority-alt-row-m-subject")).toBeVisible();
  });

  test("ungroup duplicates shows every match row", async ({page}) => {
    const state = makeAuthorityState();
    await installAuthorityMocks(page, state);
    await gotoAuthority(page);

    await page.getByLabel("Group duplicates").uncheck();
    await expect(page.getByTestId(/^authority-row-/)).toHaveCount(3);
  });

  test("search notes filters by per-record note blob", async ({page}) => {
    const state = makeAuthorityState();
    await installAuthorityMocks(page, state);
    await gotoAuthority(page);

    await page.getByRole("button", {name: "קולופון"}).click();
    await expect(page.getByTestId(/^authority-row-/)).toHaveCount(1);
    await expect(page.getByTestId("authority-row-m-author")).toBeVisible();
    await expect(page.getByTestId("authority-row-m-other")).not.toBeVisible();
  });

  test("matching help includes Hebrew curator section", async ({page}) => {
    const state = makeAuthorityState();
    await installAuthorityMocks(page, state);
    await gotoAuthority(page);

    const help = page.locator("summary", {hasText: "How matching works"});
    await help.scrollIntoViewIfNeeded();
    await help.click({force: true});
    await expect(page.locator("details").filter({hasText: "How matching works"}))
      .toContainText("מעדיף אישיות תג 100");
  });
});
