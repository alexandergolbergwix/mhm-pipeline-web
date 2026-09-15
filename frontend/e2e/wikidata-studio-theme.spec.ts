/**
 * E2E spec — Wikidata Studio theme behavior (Plan 2026-09-15).
 *
 * The Publication panel must follow the selected color scheme: no
 * hardcoded dark surface may remain in light mode (Rule W-35). The
 * header theme toggle re-themes the current page instantly and
 * persists across reloads via localStorage["mhm-color-scheme"].
 */

import {expect, test} from "@playwright/test";

import {
  installStudioMocks,
  makeBuildResponse,
  TEST_RUN_ID,
} from "./fixtures/wikidata-fixtures";

const LIGHT_INK = "rgb(11, 61, 38)"; // --ink under [data-theme="light"]
const DARK_INK = "rgb(234, 246, 251)"; // --ink under [data-theme="dark"]

async function gotoStudio(page: import("@playwright/test").Page, scheme: "light" | "dark") {
  await page.addInitScript((stored) => {
    // Seed once per page: a reload must keep the curator's toggled scheme.
    if (localStorage.getItem("mhm-theme-seeded") === "1") return;
    localStorage.setItem("mhm-color-scheme", stored);
    localStorage.setItem("mhm-theme-seeded", "1");
    localStorage.setItem("mhm.studio.reviewMode", "modern");
  }, scheme);
  await installStudioMocks(page, makeBuildResponse());
  await page.goto(`/runs/${TEST_RUN_ID}/wikidata-studio`);
  await page.getByTestId("wikidata-publication-panel").waitFor({timeout: 15_000});
}

test.describe("Wikidata Studio theme", () => {
  test("light mode renders the Publication panel with light tokens", async ({page}) => {
    await gotoStudio(page, "light");

    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    const panel = page.getByTestId("wikidata-publication-panel");
    await expect(panel).toHaveCSS("color", LIGHT_INK);

    // No element on the page keeps the old hardcoded near-black surface.
    const hardcodedDark = await page
      .locator('[data-testid="wikidata-items-panel"] [class*="bg-slate-9"]')
      .count();
    expect(hardcodedDark).toBe(0);

    await page.screenshot({path: "/tmp/wikidata-studio-light.png", fullPage: true});
  });

  test("header toggle switches to dark and persists after reload", async ({page}) => {
    await gotoStudio(page, "light");

    const panel = page.getByTestId("wikidata-publication-panel");
    await expect(panel).toHaveCSS("color", LIGHT_INK);

    await page.getByRole("radio", {name: "Dark", exact: true}).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(panel).toHaveCSS("color", DARK_INK);

    await page.reload();
    await page.getByTestId("wikidata-publication-panel").waitFor({timeout: 15_000});
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    await page.screenshot({path: "/tmp/wikidata-studio-dark.png", fullPage: true});
  });
});
